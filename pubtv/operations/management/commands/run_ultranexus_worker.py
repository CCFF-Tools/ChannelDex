import time
import fcntl
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from pubtv.operations.automation import process_due_job_state, process_preparation_job
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
        PreparationJob.objects.filter(status="running").update(
            status="failed", finished_at=now,
            error="Worker interruption detected; review remote state before an explicit retry.",
        )
        PublicationJob.objects.filter(status="running").update(
            status="failed", finished_at=now,
            error="Worker interruption left publication state uncertain; automatic retry is prohibited.",
        )
        poll_seconds = max(0.25, options["poll_seconds"])
        try:
            while True:
                processed = False
                preparation = self._claim_preparation()
                if preparation:
                    process_preparation_job(preparation)
                    processed = True
                publication = self._claim_publication()
                if publication:
                    process_due_job_state(publication)
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
