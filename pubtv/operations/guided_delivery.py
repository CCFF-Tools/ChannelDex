"""Guided already-encoded episode delivery orchestration.

The module only coordinates durable records. Long-running media, controller,
FTP, and activation work is still performed by the existing worker/services;
those services remain the source of truth for their safety gates.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import uuid

from django.db import transaction
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .automation import (approve_preparation_batch, canonical_hash,
                         schedule_ready_binding)
from .models import (AuditEvent, Device, Episode, GuidedEpisodeDelivery,
                     MediaAsset, PreparationBatch, PreparationBatchItem,
                     PreparationJob, PublicationJob, Show,
                     WeeklyEpisodeAssignment)
from .schedule_preparation import confirm_schedule_preparation, preview_schedule_preparation
from .services import materialize_assignment


def _file_snapshot(path: str) -> dict:
    candidate = Path(path).expanduser().resolve(strict=True)
    if not candidate.is_file() or candidate.stat().st_size == 0:
        raise ValueError("The selected encoded episode must be a non-empty file")
    from .automation import _sha256_file
    stat = candidate.stat()
    return {"path": str(candidate), "name": candidate.name,
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": _sha256_file(candidate)}


@transaction.atomic
def start_guided_encoded_delivery(*, target: Device, show: Show, file_path: str,
                                  episode: Episode | None = None,
                                  episode_title: str = "", actor: str = "owner",
                                  submission_token=None):
    """Create an intake review for bytes that are already encoded.

    No encoder is selected or invoked. The first worker stage still probes the
    selected bytes against the qualified target profile before transfer.
    """
    if target is None or show is None or target.pk is None or show.pk is None:
        raise ValueError("A show and target are required")
    token = uuid.UUID(str(submission_token)) if submission_token else uuid.uuid4()
    existing = GuidedEpisodeDelivery.objects.filter(token=token).first()
    if existing:
        return existing
    snapshot = _file_snapshot(file_path)
    if episode is None:
        if not episode_title.strip():
            raise ValueError("An episode title is required for a new episode")
        episode = Episode.objects.create(show=show, title=episode_title.strip())
    elif episode.show_id != show.pk:
        raise ValueError("The episode does not belong to the selected show")
    prior = [int(value[1:]) for value in MediaAsset.objects.filter(episode=episode).values_list("version", flat=True)
             if value.startswith("v") and value[1:].isdigit()]
    asset = MediaAsset.objects.create(episode=episode, file_name=snapshot["name"], kind="encoded",
                                      version=f"v{max(prior or [0]) + 1}", smb_reference=snapshot["path"])
    intake = {"target_id": target.pk, "show_id": show.pk, "episode_id": episode.pk,
              "asset_id": asset.pk, "file": snapshot, "encode_before_transfer": False}
    delivery = GuidedEpisodeDelivery.objects.create(token=token, target=target, show=show, episode=episode,
        asset=asset, intake_snapshot=intake, intake_hash=canonical_hash(intake), created_by=actor)
    AuditEvent.objects.create(actor=actor, action="create", entity="GuidedEpisodeDelivery",
                              entity_id=delivery.pk, summary="Already-encoded episode intake awaiting owner confirmation")
    return delivery


def confirm_guided_intake(delivery: GuidedEpisodeDelivery | int, *, premiere_date: date,
                          actor: str = "owner"):
    """Confirm intake and cycle plan, then queue non-blocking media work."""
    delivery = GuidedEpisodeDelivery.objects.select_related("target", "show", "episode", "asset").get(pk=getattr(delivery, "pk", delivery))
    if delivery.state not in {"intake_review", "blocked"}:
        return delivery
    expected = delivery.intake_snapshot["file"]
    current = _file_snapshot(expected["path"])
    if current["sha256"] != expected["sha256"] or current["size"] != expected["size"]:
        message = "Selected encoded bytes changed; review intake again"
        GuidedEpisodeDelivery.objects.filter(pk=delivery.pk).update(state="blocked", last_error=message)
        raise ValueError(message)
    with transaction.atomic():
        locked = GuidedEpisodeDelivery.objects.select_for_update().select_related(
            "target", "show", "episode", "asset").get(pk=delivery.pk)
        if locked.state not in {"intake_review", "blocked"}:
            return locked
        week_start = premiere_date - timedelta(days=premiere_date.weekday())
        if WeeklyEpisodeAssignment.objects.filter(show=locked.show, week_start=week_start).exists():
            raise ValueError("The selected premiere cycle is already occupied")
        batch = PreparationBatch.objects.create(target=locked.target, show=locked.show,
            submission_token=locked.token, label=f"Guided encoded: {locked.episode.title}", created_by=actor)
        PreparationBatchItem.objects.create(batch=batch, asset=locked.asset,
            selected_input_path=expected["path"], selected_input_hash=expected["sha256"],
            encode_before_transfer=False, position=0)
        # Approval 1 records the exact bytes; the worker performs strict probing.
        approve_preparation_batch(batch, actor=actor)
        batch.status = "approved"; batch.save(update_fields=["status"])
        PreparationJob.objects.get_or_create(batch=batch,
            idempotency_key=f"preparation:{batch.pk}:{batch.approval_1_hash}")
        assignment = WeeklyEpisodeAssignment.objects.create(
            show=locked.show, week_start=week_start, premiere_date=premiere_date,
            episode=locked.episode, selection_type="premiere")
        materialize_assignment(assignment)
        locked.preparation_batch = batch
        locked.cycle_assignment = assignment
        locked.state = "media_queued"
        locked.last_error = ""
        locked.save(update_fields=["preparation_batch", "cycle_assignment", "state", "last_error", "updated_at"])
        return locked


def guided_media_ready(delivery: GuidedEpisodeDelivery | int):
    delivery = GuidedEpisodeDelivery.objects.select_related("preparation_batch", "asset", "target").get(pk=getattr(delivery, "pk", delivery))
    if not delivery.preparation_batch_id:
        raise ValueError("Guided intake has not been confirmed")
    item = delivery.preparation_batch.items.order_by("pk").first()
    if not item or item.execution_status != "ready" or not item.resulting_binding_id or not schedule_ready_binding(delivery.asset, delivery.target):
        raise ValueError("Encoded media has not passed target probing and verified transfer")
    delivery.state = "media_ready"; delivery.last_error = ""
    delivery.save(update_fields=["state", "last_error", "updated_at"])
    return delivery


@transaction.atomic
def create_guided_publication(delivery: GuidedEpisodeDelivery | int, *, actor="owner"):
    """Create the preserve-mode cycle publication after media is ready."""
    delivery = GuidedEpisodeDelivery.objects.select_for_update().select_related("target", "asset").get(pk=getattr(delivery, "pk", delivery))
    if delivery.state != "media_ready":
        raise ValueError("Verified transferred media is required before cycle publication")
    assignment = delivery.cycle_assignment
    if not assignment or assignment.show_id != delivery.show_id or assignment.episode_id != delivery.episode_id:
        raise ValueError("The exact confirmed premiere-to-premiere cycle is required")
    choices = [{"assignment": assignment, "asset": delivery.asset}]
    review = preview_schedule_preparation(delivery.target, choices)
    if review["blockers"]:
        raise ValueError("; ".join(review["blockers"]))
    batch = confirm_schedule_preparation(delivery.target, choices, review["snapshot"], actor=actor)
    delivery.publication_batch = batch
    delivery.state = "cycle_planned"
    delivery.save(update_fields=["publication_batch", "state", "updated_at"])
    if delivery.show.station.auto_pull_controller_schedule:
        queue_guided_publication_jobs(delivery)
    else:
        delivery.last_error = "Automatic controller pull is disabled; authorize a fresh manual pull to continue"
        delivery.save(update_fields=["last_error", "updated_at"])
    return delivery


def queue_guided_publication_jobs(delivery, *, manual_pull_requested=False):
    """Queue one ordered pull -> NMG -> BIN -> review worker job."""
    delivery = GuidedEpisodeDelivery.objects.select_related("publication_batch").get(pk=getattr(delivery, "pk", delivery))
    if not delivery.publication_batch_id:
        raise ValueError("A publication batch is required")
    batch = delivery.publication_batch
    job, created = PublicationJob.objects.get_or_create(
        publication_batch=batch, kind="prepare_publication",
        idempotency_key=f"guided:prepare:{batch.pk}:{delivery.intake_hash}",
        defaults={"result": {"manual_pull_requested": manual_pull_requested}})
    if manual_pull_requested and not created and job.status == "failed":
        job.status = "queued"; job.error = ""; job.started_at = None; job.finished_at = None
        job.result = {"manual_pull_requested": True}
        job.save(update_fields=["status", "error", "started_at", "finished_at", "result"])
    elif manual_pull_requested and not job.result.get("manual_pull_requested"):
        raise ValueError("The existing controller preparation job requires reconciliation")
    return batch.jobs.order_by("pk")


def advance_guided_after_media(batch):
    """Advance a completed guided media job without another owner click."""
    try:
        delivery = GuidedEpisodeDelivery.objects.get(preparation_batch=batch)
    except GuidedEpisodeDelivery.DoesNotExist:
        return None
    delivery = guided_media_ready(delivery)
    return create_guided_publication(delivery)


def approve_and_queue_guided_delivery(delivery, review_token, *, actor="owner"):
    """Validate the displayed review, record Approval 2, and queue one delivery."""
    from .publication_review import validate_review_token
    with transaction.atomic():
        delivery = GuidedEpisodeDelivery.objects.select_for_update().select_related("publication_batch").get(pk=getattr(delivery, "pk", delivery))
        if delivery.state == "approval_2":
            return delivery.publication_batch.jobs.get(kind="guided_delivery")
        if delivery.state != "change_review":
            raise ValueError("A current exact controller review is required")
        review = validate_review_token(delivery.publication_batch, review_token)
        snapshot = {**__import__("pubtv.operations.automation", fromlist=["publication_snapshot"]).publication_snapshot(delivery.publication_batch), "review_hash": review["review_hash"]}
        from .automation import approve_snapshot
        approve_snapshot(delivery.publication_batch, snapshot, approval=2, actor=actor)
        job, _ = PublicationJob.objects.get_or_create(
            publication_batch=delivery.publication_batch, kind="guided_delivery",
            idempotency_key=f"guided:deliver:{delivery.publication_batch_id}:{delivery.publication_batch.approval_2_hash}")
        delivery.state = "approval_2"
        delivery.change_review = review
        delivery.change_review_hash = review["review_hash"]
        delivery.save(update_fields=["state", "change_review", "change_review_hash", "updated_at"])
        return job


def process_guided_delivery_job(job, *, stage_fn=None, activate_fn=None, already_claimed=False):
    """Worker entry point; activation is never replayed after ambiguity."""
    from .publication_delivery import stage_publication, activate_publication
    stage_fn = stage_fn or stage_publication
    activate_fn = activate_fn or activate_publication
    with transaction.atomic():
        locked = PublicationJob.objects.select_for_update().select_related("publication_batch").get(pk=job.pk)
        if locked.status in {"succeeded", "cancelled"}:
            return locked
        delivery = GuidedEpisodeDelivery.objects.select_for_update().get(publication_batch=locked.publication_batch)
        if locked.status == "running" and not already_claimed:
            return locked
        locked.status = "running"; locked.started_at = timezone.now(); locked.save(update_fields=["status", "started_at"])
    try:
        operation = delivery.publication_batch.delivery_operations.order_by("-pk").first()
        artifact = delivery.publication_batch.artifact_revisions.filter(artifact_type="bin").order_by("-revision").first()
        if not artifact:
            raise ValueError("Paired validated BIN is required before guided delivery")
        if operation is None:
            operation = stage_fn(delivery.publication_batch.pk, expected_hash=artifact.content_hash)
            GuidedEpisodeDelivery.objects.filter(pk=delivery.pk).update(state="staged", candidate_hash=artifact.content_hash, current_hash=operation.base_hash, rollback_hash=operation.rollback_hash)
        if operation.state != "staged":
            raise ValueError("Guided delivery operation is not safely staged; reconcile before retry")
        operation = activate_fn(operation.pk, expected_hash=artifact.content_hash)
        record_guided_delivery(delivery, operation)
        locked.status = "succeeded"; locked.error = ""; locked.finished_at = timezone.now()
        locked.result = {"operation_id": operation.pk, "candidate_sha256": artifact.content_hash}
        locked.save(update_fields=["status", "error", "finished_at", "result"])
    except Exception as exc:
        locked.status = "failed"; locked.error = str(exc); locked.finished_at = timezone.now()
        locked.save(update_fields=["status", "error", "finished_at"])
        GuidedEpisodeDelivery.objects.filter(pk=delivery.pk).update(last_error=str(exc))
    return locked


def guided_delivery_versions(delivery: GuidedEpisodeDelivery | int) -> dict:
    """Return candidate/current/rollback versions for owner-facing UI."""
    delivery = GuidedEpisodeDelivery.objects.select_related("publication_batch").get(pk=getattr(delivery, "pk", delivery))
    operation = delivery.publication_batch.delivery_operations.order_by("-pk").first() if delivery.publication_batch_id else None
    return {"candidate": delivery.candidate_hash, "current": delivery.current_hash,
            "rollback": delivery.rollback_hash,
            "candidate_path": operation.staging_path if operation else "",
            "rollback_path": operation.rollback_path if operation else "",
            "remote_backup_path": operation.remote_backup_path if operation else ""}


def record_guided_delivery(delivery: GuidedEpisodeDelivery | int, operation, *, actor="owner"):
    """Mirror an already completed attended operation without replaying it."""
    with transaction.atomic():
        delivery = GuidedEpisodeDelivery.objects.select_for_update().get(pk=getattr(delivery, "pk", delivery))
        if delivery.activation_count:
            raise ValueError("Guided activation has already been requested")
        delivery.candidate_hash = operation.artifact.content_hash
        delivery.current_hash = operation.artifact.content_hash
        delivery.rollback_hash = operation.rollback_hash
        delivery.activation_count = 1
        delivery.verification_prompted = True
        delivery.state = "verification_pending"
        delivery.save(update_fields=["candidate_hash", "current_hash", "rollback_hash", "activation_count", "verification_prompted", "state", "updated_at"])
        return delivery


def record_guided_observation(delivery: GuidedEpisodeDelivery | int, *, operation_state: str):
    with transaction.atomic():
        delivery = GuidedEpisodeDelivery.objects.select_for_update().get(pk=getattr(delivery, "pk", delivery))
        if delivery.state != "verification_pending" or operation_state != "activation_observed":
            raise ValueError("Independent activation evidence is required before coverage")
        delivery.state = "verified"
        delivery.save(update_fields=["state", "updated_at"])
        return delivery


def record_guided_rollback(delivery: GuidedEpisodeDelivery | int, operation, *, actor="owner"):
    with transaction.atomic():
        delivery = GuidedEpisodeDelivery.objects.select_for_update().get(pk=getattr(delivery, "pk", delivery))
        delivery.current_hash = operation.rollback_hash
        delivery.state = "rolled_back"
        delivery.last_error = "Active schedule restored to the retained known-good revision"
        delivery.save(update_fields=["current_hash", "state", "last_error", "updated_at"])
        return delivery


def guided_delivery_view(request):
    """Owner-facing entry and progress page; workers remain asynchronous."""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "start":
                target = get_object_or_404(Device, pk=request.POST.get("target"))
                show = get_object_or_404(Show, pk=request.POST.get("show"))
                episode = Episode.objects.filter(pk=request.POST.get("episode"), show=show).first()
                delivery = start_guided_encoded_delivery(target=target, show=show,
                    episode=episode, episode_title=request.POST.get("episode_title", ""),
                    file_path=request.POST.get("file_path", ""),
                    submission_token=request.POST.get("submission_token"))
                messages.success(request, "Encoded intake recorded. Review the exact file, episode, target, and premiere cycle before confirmation.")
                return redirect("guided-delivery")
            delivery = get_object_or_404(GuidedEpisodeDelivery, pk=request.POST.get("delivery"))
            if action == "confirm":
                confirm_guided_intake(delivery, premiere_date=date.fromisoformat(request.POST["premiere_date"]))
                messages.success(request, "Premiere cycle confirmed and target-profile media work queued.")
            elif action == "mark_media_ready":
                guided_media_ready(delivery)
                messages.success(request, "Verified media accepted. Create the preserve-mode publication when ready.")
            elif action == "create_publication":
                create_guided_publication(delivery)
                messages.success(request, "Publication created. Continue in Schedule delivery for fresh controller review and Approval 2.")
            elif action == "approve_and_deliver":
                approve_and_queue_guided_delivery(delivery, request.POST.get("review_token", ""))
                messages.success(request, "Changes approved. One durable guided delivery job was queued; activation remains worker-owned.")
            elif action == "queue_manual_pull":
                if delivery.show.station.auto_pull_controller_schedule:
                    raise ValueError("Automatic controller pulling is already enabled")
                queue_guided_publication_jobs(delivery, manual_pull_requested=True)
                delivery.last_error = ""
                delivery.save(update_fields=["last_error", "updated_at"])
                messages.success(request, "Fresh manual controller pull authorized and queued.")
            elif action == "verify_activation":
                if request.POST.get("confirm_observation") != "on":
                    raise ValueError("Confirm the independent controller observation")
                operation = delivery.publication_batch.delivery_operations.order_by("-pk").first()
                if not operation or operation.artifact.content_hash != delivery.candidate_hash:
                    raise ValueError("The delivered candidate is unavailable or changed")
                from .publication_delivery import observe_activation
                operation = observe_activation(operation.pk,
                    evidence_file=request.POST.get("evidence_file", ""))
                record_guided_observation(delivery, operation_state=operation.state)
                messages.success(request, "Independent activation evidence accepted and exact schedule coverage recorded.")
            elif action == "rollback":
                if request.POST.get("confirm_rollback") != "on":
                    raise ValueError("Confirm the retained known-good BIN before rollback")
                operation = delivery.publication_batch.delivery_operations.order_by("-pk").first()
                if not operation or operation.rollback_hash != delivery.rollback_hash:
                    raise ValueError("The retained rollback version is unavailable or changed")
                from .publication_delivery import rollback_publication
                operation = rollback_publication(operation.pk,
                    expected_rollback_hash=delivery.rollback_hash)
                record_guided_rollback(delivery, operation)
                messages.success(request, "Known-good BIN restored and LOADSCH acknowledged.")
            return redirect("guided-delivery")
        except (ValueError, OSError, TypeError, KeyError) as exc:
            messages.error(request, str(exc))
    deliveries = list(GuidedEpisodeDelivery.objects.select_related(
        "target", "show", "episode", "asset", "preparation_batch", "publication_batch",
        "cycle_assignment").order_by("-created_at")[:20])
    for delivery in deliveries:
        delivery.guided_versions = guided_delivery_versions(delivery)
        delivery.operation = (delivery.publication_batch.delivery_operations.order_by("-pk").first()
                              if delivery.publication_batch_id else None)
        if delivery.publication_batch_id:
            from .publication_review import publication_review
            delivery.guided_review = publication_review(delivery.publication_batch)
    return render(request, "guided_delivery.html", {
        "deliveries": deliveries, "targets": Device.objects.order_by("name"),
        "shows": Show.objects.order_by("title"),
        "episodes": Episode.objects.select_related("show").order_by("show__title", "title"),
        "submission_token": uuid.uuid4(),
    })
