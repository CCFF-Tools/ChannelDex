import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pubtv.operations.models import Device, ResearchGate


class Command(BaseCommand):
    help = "Record an evidence-backed UltraNEXUS research-gate result."

    def add_arguments(self, parser):
        parser.add_argument("target", type=int, help="Device primary key")
        parser.add_argument("key")
        parser.add_argument("evidence_file")
        parser.add_argument("--status", choices=("passed", "blocked"), required=True)
        parser.add_argument("--actor", default="owner")

    def handle(self, *args, **options):
        target = Device.objects.filter(pk=options["target"]).first()
        if not target:
            raise CommandError("Target device does not exist.")
        path = Path(options["evidence_file"]).expanduser().resolve()
        if not path.is_file():
            raise CommandError("Evidence file does not exist.")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        evidence = f"{path}\nsha256:{digest}"
        gate, _ = ResearchGate.objects.update_or_create(
            target=target,
            key=options["key"],
            defaults={
                "status": options["status"],
                "evidence": evidence,
                "resolved_at": timezone.now(),
                "resolved_by": options["actor"],
            },
        )
        self.stdout.write(self.style.SUCCESS(f"{gate.key}: {gate.status} ({digest})"))
