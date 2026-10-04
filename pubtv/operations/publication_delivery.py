"""Attended, separately confirmed UltraNEXUS schedule delivery."""
from __future__ import annotations

import hashlib
import json
import socket
import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone

from .models import (ActivationEvidence, ArtifactRevision, Device, ResearchGate, UploadedScheduleRevision,
                     ScheduleDeliveryOperation, SchedulePublicationBatch)
from .automation import (canonical_hash, publication_artifact_blockers,
                         publication_snapshot, restricted_target_blockers,
                         settings_record_snapshot, _keychain_password, invalidate_snapshot)
from .services import create_upload_snapshot
from pubtv.ultranexus.bin import BinImage
from pubtv.ultranexus.command import ControllerCommandTransport, CommandError, LOADSCH_PATH
from pubtv.ultranexus.delivery import RecoverableFTPAdapter, stage_schedule, promote_staged_schedule
from pubtv.ultranexus.ftp import StdlibFTPAdapter
from pubtv.ultranexus.utils import sha256_file


DELIVERY_GATES = ("nmg_bin_relationship", "schedule_bin_format", "schedule_bin_activation",
                  "ftp_promotion_recovery")


def _preflight(batch: SchedulePublicationBatch):
    if batch.status == "cancelled":
        raise ValueError("Cancelled publications cannot be staged or activated")
    current = batch.target.ultranexus_settings.filter(is_current=True).first()
    target_blockers = [item for item in restricted_target_blockers(current)
                       if "base_bin_hash" not in item and "base_nmg_hash" not in item]
    if target_blockers:
        raise ValueError("Qualified target contract is incomplete: " + "; ".join(target_blockers))
    if batch.workflow_mode != "selected_changes" or batch.reconciliation_mode != "preserve":
        raise ValueError("Only attended selected preserved changes are qualified")
    if batch.requested_activation_at:
        raise ValueError("Future activation is unavailable in the restricted release")
    if batch.approval_2_status != "approved" or batch.approval_2_hash != canonical_hash(batch.approval_2_snapshot):
        raise ValueError("Approval 2 is required")
    if publication_snapshot(batch) != batch.approval_2_snapshot or publication_artifact_blockers(batch):
        raise ValueError("Approval 2 is stale or its artifacts changed")
    if not batch.controller_snapshot_id or batch.controller_snapshot_hash != batch.controller_snapshot.snapshot_hash:
        raise ValueError("A fresh batch-bound controller BIN pull is required")
    from .publication_review import publication_review
    review = publication_review(batch)
    if review.get("blockers") or review.get("review_hash") != batch.approval_2_snapshot.get("review_hash"):
        raise ValueError("Approval 2 requires the current captured controller review")
    artifact = batch.artifact_revisions.filter(artifact_type="bin").order_by("-revision").first()
    if not artifact or sha256_file(artifact.file_reference) != artifact.content_hash:
        raise ValueError("Reviewed BIN artifact is missing or changed")
    BinImage.from_file(artifact.file_reference, expected_sha256=artifact.content_hash)
    passed = set(ResearchGate.objects.filter(target=batch.target, status="passed").values_list("key", flat=True))
    required = set(DELIVERY_GATES)
    if artifact.manifest.get("introduced_resource_references"):
        required.add("resource_registration")
    missing = sorted(required - passed)
    if missing:
        raise ValueError("Target qualification is incomplete: " + ", ".join(missing))
    if not current.host or not current.port or not current.secret_reference:
        raise ValueError("FTP target configuration is incomplete")
    return current, artifact


def _ftp(current, *, ftp_factory=StdlibFTPAdapter, secret_resolver=_keychain_password):
    secret = secret_resolver(current.secret_reference)
    creds = secret if isinstance(secret, dict) else {"password": secret}
    return RecoverableFTPAdapter(ftp_factory(current.host, port=current.port,
                                              username=creds.get("username") or current.settings.get("ftp_username"),
                                              password=creds.get("password")))


def _evidence(operation, status, *, detail=None, actor="owner"):
    return ActivationEvidence.objects.create(
        publication_batch=operation.publication_batch, target=operation.target,
        status=status, actor=actor,
        details={"operation_id": operation.pk, "artifact_sha256": operation.artifact.content_hash,
                 **(detail or {})},
    )


def stage_publication(batch_id: int, *, expected_hash: str, actor="owner",
                      ftp_factory=StdlibFTPAdapter, secret_resolver=_keychain_password):
    """Capture the current remote BIN and stage a candidate; leave live untouched."""
    with transaction.atomic():
        initial = SchedulePublicationBatch.objects.get(pk=batch_id)
        Device.objects.select_for_update().get(pk=initial.target_id)
        batch = SchedulePublicationBatch.objects.select_for_update().select_related("target").get(pk=batch_id)
        current, artifact = _preflight(batch)
        if expected_hash.lower() != artifact.content_hash:
            raise ValueError("Candidate hash confirmation does not match")
        if ScheduleDeliveryOperation.objects.filter(target=batch.target,
                state__in=ScheduleDeliveryOperation.ACTIVE).exists():
            raise ValueError("Another schedule operation requires review")
        operation = ScheduleDeliveryOperation.objects.create(
            publication_batch=batch, target=batch.target, artifact=artifact,
            approval_hash=batch.approval_2_hash, base_hash=batch.controller_snapshot.payload.get("captured_sha256", ""),
            actor=actor,
        )
    root = Path(django_settings.DATA_DIR) / "ultranexus" / "rollback"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    rollback = root / f"operation-{operation.pk}-{uuid.uuid4().hex}.bin"
    try:
        adapter = _ftp(current, ftp_factory=ftp_factory, secret_resolver=secret_resolver)
        adapter.adapter.download(LOADSCH_PATH, str(rollback))
        if sha256_file(rollback) != operation.base_hash:
            raise ValueError("Remote schedule differs from the approved known-good base")
        BinImage.from_file(rollback, expected_sha256=operation.base_hash)
        staged = stage_schedule(adapter, artifact.file_reference, LOADSCH_PATH)
        if adapter.read_hash(staged) != artifact.content_hash:
            raise ValueError("Staged remote schedule differs from the approved BIN")
        operation.rollback_path = str(rollback)
        operation.rollback_hash = operation.base_hash
        operation.staging_path = staged
        operation.state = "staged"
        operation.result = {"candidate_sha256": artifact.content_hash, "remote_base_sha256": operation.base_hash}
        operation.save(update_fields=["rollback_path", "rollback_hash", "staging_path", "state", "result", "updated_at"])
        return operation
    except Exception as exc:
        # Any live-BIN drift invalidates the reviewed batch. Recovery requires
        # a fresh pull, regeneration, and a new Approval 2; never rebase.
        batch.approval_2_status = "stale"
        batch.status = "blocked"
        batch.save(update_fields=["approval_2_status", "status"])
        known_drift = isinstance(exc, ValueError) and str(exc) in {
            "Remote schedule differs from the approved known-good base",
            "Staged remote schedule differs from the approved BIN",
        }
        operation.state = "failed" if known_drift else "ambiguous"
        operation.result = ({"blocked": "Remote schedule or staged bytes do not match the approved hashes"}
                            if known_drift else
                            {"review_required": "Inspect staging and remote schedule before another attempt"})
        operation.save(update_fields=["state", "result", "updated_at"])
        raise


def _activate_command(current, *, secret_resolver=_keychain_password, socket_factory=socket.create_connection):
    if not current.command_username or not current.command_secret_reference:
        raise ValueError("Command username and Keychain reference are required")
    password = secret_resolver(current.command_secret_reference)
    if isinstance(password, dict): password = password.get("password")
    if not password:
        raise ValueError("Command password is unavailable")
    truncated = len(password) > 8
    sock = socket_factory((current.host, current.command_port), timeout=10)
    try:
        client = ControllerCommandTransport(sock)
        client.authenticate(current.command_username, password)
        return client.activate_schedule(), truncated
    finally:
        sock.close()


def activate_publication(operation_id: int, *, expected_hash: str, actor="owner",
                         ftp_factory=StdlibFTPAdapter, secret_resolver=_keychain_password,
                         socket_factory=socket.create_connection):
    """Promote once, then send exactly one LOADSCH in this confirmation."""
    with transaction.atomic():
        initial = ScheduleDeliveryOperation.objects.get(pk=operation_id)
        Device.objects.select_for_update().get(pk=initial.target_id)
        SchedulePublicationBatch.objects.select_for_update().get(pk=initial.publication_batch_id)
        operation = ScheduleDeliveryOperation.objects.select_for_update().select_related(
            "publication_batch", "target", "artifact").get(pk=operation_id)
        if operation.state != "staged":
            raise ValueError("A staged operation is required")
        current, artifact = _preflight(operation.publication_batch)
        if (artifact.pk != operation.artifact_id or expected_hash.lower() != artifact.content_hash or
                operation.approval_hash != operation.publication_batch.approval_2_hash or
                operation.base_hash != operation.rollback_hash):
            raise ValueError("Staged approval or candidate changed")
        if sha256_file(operation.rollback_path) != operation.rollback_hash:
            raise ValueError("Known-good rollback artifact changed")
        operation.remote_backup_path = f"{LOADSCH_PATH}.{uuid.uuid4().hex}.bak"
        operation.state = "promotion_started"
        operation.save(update_fields=["remote_backup_path", "state", "updated_at"])
    try:
        adapter = _ftp(current, ftp_factory=ftp_factory, secret_resolver=secret_resolver)
        if adapter.read_hash(LOADSCH_PATH) != operation.rollback_hash:
            raise ValueError("Live remote schedule changed after staging")
        result = promote_staged_schedule(adapter, operation.staging_path, LOADSCH_PATH,
                                         expected_hash=artifact.content_hash,
                                         backup_path=operation.remote_backup_path)
        operation.remote_backup_path = result.backup_path or ""
        operation.state = "schedule_transferred"
        operation.save(update_fields=["remote_backup_path", "state", "updated_at"])
        _evidence(operation, "schedule_transferred", actor=actor)
        if not operation.remote_backup_path:
            raise ValueError("Remote known-good backup is unavailable")
        # This durable state is deliberately conservative: a crash before send
        # still requires reconciliation rather than an automatic retry.
        operation.state = "activation_requested"
        operation.save(update_fields=["state", "updated_at"])
        _evidence(operation, "activation_requested", actor=actor)
        _response, truncated = _activate_command(current, secret_resolver=secret_resolver,
                                                 socket_factory=socket_factory)
        operation.state = "activation_acknowledged"
        operation.result = {**operation.result, "xpass_password_truncated": truncated}
        operation.save(update_fields=["state", "result", "updated_at"])
        _evidence(operation, "activation_acknowledged", detail={"response_code": "200"}, actor=actor)
        return operation
    except Exception:
        # Before LOADSCH, a failed promotion may leave the live name vacant.
        # Restore only when both backup bytes and absence of the live name are
        # established. Connection or permission failures are not absence.
        restored = False
        if operation.state == "promotion_started" and operation.remote_backup_path:
            try:
                backup_ok = adapter.read_hash(operation.remote_backup_path) == operation.rollback_hash
                live_missing = adapter.is_missing(LOADSCH_PATH)
                if backup_ok and live_missing:
                    adapter.promote(operation.remote_backup_path, LOADSCH_PATH)
                    restored = adapter.read_hash(LOADSCH_PATH) == operation.rollback_hash
            except Exception:
                pass
        if restored:
            operation.state = "failed"
            operation.result = {"restored_before_activation": True}
            operation.save(update_fields=["state", "result", "updated_at"])
            _evidence(operation, "failed", detail={"known_good_restored": True}, actor=actor)
            raise
        operation.state = "ambiguous"
        operation.result = {"review_required": "Check live, backup, and controller activation state; do not retry LOADSCH blindly"}
        operation.save(update_fields=["state", "result", "updated_at"])
        _evidence(operation, "ambiguous", actor=actor)
        raise


def observe_activation(operation_id: int, *, evidence_file: str, actor="owner"):
    """Attach independent operator or controller evidence; then record coverage."""
    with transaction.atomic():
        operation = ScheduleDeliveryOperation.objects.select_for_update().select_related(
            "publication_batch", "target", "artifact").get(pk=operation_id)
        if operation.state != "activation_acknowledged":
            raise ValueError("An acknowledged activation is required")
        if not evidence_file or not Path(evidence_file).is_file():
            raise ValueError("Independent activation evidence file is required")
        try:
            observation = json.loads(Path(evidence_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Activation observation must be a readable JSON evidence file") from exc
        if (not isinstance(observation, dict)
                or observation.get("target_id") != operation.target_id
                or observation.get("artifact_sha256") != operation.artifact.content_hash
                or observation.get("schedule_path") != LOADSCH_PATH
                or observation.get("status") != "observed_active"
                or not observation.get("observed_at")
                or observation.get("source") not in {"controller", "winlgx", "operator_observation"}):
            raise ValueError("Activation evidence does not independently identify this target and BIN")
        try:
            observed_at = datetime.fromisoformat(str(observation["observed_at"]).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Activation evidence requires a valid ISO-8601 observation time") from exc
        if not timezone.is_aware(observed_at):
            raise ValueError("Activation evidence observation time must include a UTC offset")
        digest = sha256_file(evidence_file)
        ActivationEvidence.objects.create(
            publication_batch=operation.publication_batch, target=operation.target,
            status="activation_observed", evidence_hash=digest,
            external_reference=evidence_file, actor=actor,
            details={"operation_id": operation.pk, "artifact_sha256": operation.artifact.content_hash,
                     "source": observation["source"], "observed_at": observation["observed_at"]},
        )
        operation.state = "activation_observed"
        operation.save(update_fields=["state", "updated_at"])
        batch = operation.publication_batch
        batch.activated_at = timezone.now()
        batch.status = "verified"
        batch.save(update_fields=["activated_at", "status"])
        selected = [entry.occurrence for entry in batch.occurrence_selections.select_for_update().select_related("occurrence")
                    if entry.operation != "delete" and entry.occurrence.revision == entry.occurrence_revision]
        if selected:
            create_upload_snapshot(device=operation.target, occurrences=selected,
                                   external_reference=operation.artifact.content_hash, actor=actor)
        return operation


def rollback_publication(operation_id: int, *, expected_rollback_hash: str, actor="owner",
                         ftp_factory=StdlibFTPAdapter, secret_resolver=_keychain_password,
                         socket_factory=socket.create_connection):
    """Operator-confirmed restoration; uncertain results stay ambiguous."""
    with transaction.atomic():
        operation = ScheduleDeliveryOperation.objects.select_for_update().select_related(
            "publication_batch", "target", "artifact").get(pk=operation_id)
        Device.objects.select_for_update().get(pk=operation.target_id)
        current = operation.target.ultranexus_settings.filter(is_current=True).first()
        if not current or operation.state not in {"schedule_transferred", "activation_requested",
                                                  "activation_acknowledged", "activation_observed", "ambiguous"}:
            raise ValueError("This operation has no reviewed rollback action")
        approved_settings = operation.publication_batch.approval_2_snapshot.get("target_settings")
        if settings_record_snapshot(current) != approved_settings:
            raise ValueError("Target settings changed after approval; rollback requires manual reconciliation")
        if (not operation.remote_backup_path or not operation.rollback_path or
                expected_rollback_hash.lower() != operation.rollback_hash or
                sha256_file(operation.rollback_path) != operation.rollback_hash):
            raise ValueError("Known-good rollback hash or artifact changed")
        operation.state = "rollback_started"
        operation.save(update_fields=["state", "updated_at"])
    try:
        adapter = _ftp(current, ftp_factory=ftp_factory, secret_resolver=secret_resolver)
        if adapter.read_hash(operation.remote_backup_path) != operation.rollback_hash:
            raise ValueError("Remote rollback backup hash changed")
        adapter.rollback(operation.staging_path, LOADSCH_PATH, operation.remote_backup_path)
        if adapter.read_hash(LOADSCH_PATH) != operation.rollback_hash:
            raise ValueError("Restored remote BIN hash mismatch")
        _activate_command(current, secret_resolver=secret_resolver, socket_factory=socket_factory)
        operation.state = "rolled_back"
        operation.save(update_fields=["state", "updated_at"])
        _evidence(operation, "rolled_back", detail={"response_code": "200"}, actor=actor)
        UploadedScheduleRevision.objects.filter(
            device=operation.target, external_reference=operation.artifact.content_hash,
            state="active",
        ).update(state="invalidated", supersession_reason="UltraNEXUS schedule rollback")
        batch = operation.publication_batch
        batch.status = "blocked"
        batch.save(update_fields=["status"])
        return operation
    except Exception:
        operation.state = "rollback_ambiguous"
        operation.save(update_fields=["state", "updated_at"])
        _evidence(operation, "ambiguous", detail={"phase": "rollback"}, actor=actor)
        raise
