"""Dry-run-first cleanup for generated UltraNEXUS schedule artifacts."""
import json

from django.core.management.base import BaseCommand

from pubtv.operations.artifact_retention import DEFAULT_KEEP_SETS, cleanup, set_pinned
from pubtv.operations.models import SchedulePublicationBatch


class Command(BaseCommand):
    help = "Plan (or, with --execute, delete) old generated UltraNEXUS artifacts."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true",
                            help="Delete eligible files after safety checks; default is dry-run.")
        parser.add_argument("--keep-sets", type=int, default=DEFAULT_KEEP_SETS,
                            help="Number of newest unpinned successful batch sets per target to retain (minimum 20).")
        parser.add_argument("--actor", default="owner")
        parser.add_argument("--pin", type=int, metavar="BATCH_ID")
        parser.add_argument("--unpin", type=int, metavar="BATCH_ID")

    def handle(self, *args, **options):
        if options["pin"] and options["unpin"]:
            self.stderr.write(self.style.ERROR("Choose only one of --pin or --unpin."))
            return
        pin_id = options["pin"] or options["unpin"]
        if pin_id:
            if not SchedulePublicationBatch.objects.filter(pk=pin_id).exists():
                self.stderr.write(self.style.ERROR("Publication batch does not exist."))
                return
            set_pinned(pin_id, pinned=bool(options["pin"]), actor=options["actor"])
            self.stdout.write(self.style.SUCCESS(f"Artifact set {pin_id} {'pinned' if options['pin'] else 'unpinned'}"))
            return
        if options["keep_sets"] < DEFAULT_KEEP_SETS:
            self.stderr.write(self.style.ERROR("--keep-sets must be at least 20."))
            return
        report = cleanup(execute=options["execute"], keep_sets=options["keep_sets"], actor=options["actor"])
        mode = "executed" if options["execute"] else "dry-run"
        self.stdout.write(f"UltraNEXUS artifact retention {mode}: {len(report.get('actions', []))} eligible")
        self.stdout.write(json.dumps(report, sort_keys=True, indent=2))
