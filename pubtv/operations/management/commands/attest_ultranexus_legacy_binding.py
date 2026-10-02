import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from pubtv.operations.models import Device, MediaBinding, SchedulePublicationBatch


class Command(BaseCommand):
    help = "Attest a pre-existing UltraNEXUS media binding with private evidence."

    def add_arguments(self, parser):
        parser.add_argument("binding", type=int)
        parser.add_argument("evidence_file")
        parser.add_argument("--actor", default="owner")

    def handle(self, *args, **options):
        binding = MediaBinding.objects.filter(pk=options["binding"], verification_basis="legacy").first()
        if not binding:
            raise CommandError("Legacy media binding does not exist.")
        if not binding.external_reference:
            raise CommandError("Legacy media binding requires its controller reference before attestation.")
        path = Path(options["evidence_file"]).expanduser().resolve()
        if not path.is_file():
            raise CommandError("Evidence file does not exist.")
        with transaction.atomic():
            Device.objects.select_for_update().get(pk=binding.target_id)
            binding = MediaBinding.objects.select_for_update().get(pk=binding.pk)
            binding.legacy_attestation_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            binding.legacy_attested_at = timezone.now()
            binding.legacy_attested_by = options["actor"]
            binding.notes = "\n".join(filter(None, (binding.notes, f"Attestation evidence: {path}")))
            binding.full_clean()
            binding.save(update_fields=["legacy_attestation_hash", "legacy_attested_at", "legacy_attested_by", "notes"])
            SchedulePublicationBatch.objects.filter(
                target_id=binding.target_id,
                approval_2_status="approved",
                occurrence_selections__occurrence__asset_id=binding.asset_id,
            ).update(approval_2_status="stale")
        self.stdout.write(self.style.SUCCESS(f"Attested legacy binding {binding.pk}: {binding.legacy_attestation_hash}"))
