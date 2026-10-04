import time
import fcntl
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from pubtv.operations.automation import process_due_job_state, process_preparation_job
from pubtv.operations.services import reconcile_passed_premieres
from pubtv.operations.models import PreparationJob, PublicationJob


class Command(BaseCommand):
    help = "Run the supervised local UltraNEXUS worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process currently eligible jobs, then exit.")
        parser.add_argument("--poll-seconds", type=float, default=5.0)

    def _claim_preparation(self):
        with transaction.atomic():
            job = PreparationJob.objects.select_for_update().filter(status="queued").order_by("queued_at", "pk").first()
            if job:
                job.status = "running"
                job.started_at = timezone.now()
                job.save(update_fields=["status", "started_at"])
            return job

    def _claim_publication(self):
        now = timezone.now()
        with transaction.atomic():
            job = PublicationJob.objects.select_for_update().filter(status="queued").filter(
                Q(publication_batch__requested_activation_at__isnull=True)
                | Q(publication_batch__requested_activation_at__lte=now)
            ).order_by("queued_at", "pk").first()
            if job:
                job.status = "running"
                job.started_at = now
                job.save(update_fields=["status", "started_at"])
            return job

    def handle(self, *args, **options):
        lock_dir = Path(settings.DATA_DIR) / "ultranexus"
        lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_handle = open(lock_dir / "worker.lock", "a+")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_handle.close()
            raise CommandError("Another UltraNEXUS worker is already running.") from exc
        now = timezone.now()
        interrupted = PreparationJob.objects.filter(status="running")
        interrupted_jobs = list(interrupted)
        interrupted_batches = [job.batch_id for job in interrupted_jobs]
        interrupted.update(
            status="failed", finished_at=now,
            error="Needs attention: worker interruption detected; review remote state before an explicit retry.",
        )
        # Never replay uncertain transfers.  Leave each item visibly blocked
        # for an operator to inspect and retry individually.
        from pubtv.operations.models import PreparationBatchItem
        if interrupted_batches:
            PreparationBatchItem.objects.filter(batch_id__in=interrupted_batches, execution_status__in=("encoding", "validating", "transferring", "verifying")).update(
                execution_status="blocked", blocker="Needs attention: worker interruption; explicit item retry required."
            )
            # Untouched rows are safe to continue; uncertain rows stay blocked.
            for job in interrupted_jobs:
                pending = list(PreparationBatchItem.objects.filter(
                    batch_id=job.batch_id, execution_status="pending"
                ).values_list("pk", flat=True))
                if pending:
                    PreparationJob.objects.get_or_create(
                        idempotency_key=f"preparation-recovery:{job.pk}",
                        defaults={"batch_id": job.batch_id, "result": {"item_ids": pending}},
                    )
        PublicationJob.objects.filter(status="running").update(
            status="failed", finished_at=now,
            error="Worker interruption left publication state uncertain; automatic retry is prohibited.",
        )
        poll_seconds = max(0.25, options["poll_seconds"])
        try:
            reconcile_passed_premieres()
            while True:
                processed = False
                processed = bool(reconcile_passed_premieres())
                preparation = self._claim_preparation()
                if preparation:
                    process_preparation_job(preparation)
                    processed = True
                publication = self._claim_publication()
                if publication:
                    process_due_job_state(publication, already_claimed=True)
                    processed = True
                if options["once"]:
                    if not processed:
                        self.stdout.write("No eligible UltraNEXUS jobs.")
                    return
                if not processed:
                    time.sleep(poll_seconds)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
