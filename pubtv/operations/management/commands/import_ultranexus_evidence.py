import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max

from pubtv.operations.models import ArtifactRevision, ControllerSnapshot, Device, SchedulePublicationBatch
from pubtv.ultranexus.nmg import validate_nmg


class Command(BaseCommand):
    help = "Import a captured controller snapshot or validated NMG/BIN artifact by hash."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="kind", required=True)
        snapshot = subparsers.add_parser("snapshot")
        snapshot.add_argument("target", type=int)
        snapshot.add_argument("file")
        artifact = subparsers.add_parser("artifact")
        artifact.add_argument("publication_batch", type=int)
        artifact.add_argument("artifact_type", choices=("nmg", "bin"))
        artifact.add_argument("file")
        artifact.add_argument("--validation-file")

    @staticmethod
    def _file(value):
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise CommandError(f"File does not exist: {path}")
        return path

    def handle(self, *args, **options):
        path = self._file(options["file"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if options["kind"] == "snapshot":
            target = Device.objects.filter(pk=options["target"]).first()
            if not target:
                raise CommandError("Target device does not exist.")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CommandError("Controller snapshot must be a JSON file.") from exc
            with transaction.atomic():
                target = Device.objects.select_for_update().get(pk=target.pk)
                revision = (target.controller_snapshots.aggregate(value=Max("revision"))["value"] or 0) + 1
                ControllerSnapshot.objects.create(
                    target=target,
                    revision=revision,
                    snapshot_hash=digest,
                    payload=payload,
                    source_reference=str(path),
                )
                SchedulePublicationBatch.objects.filter(
                    target=target, approval_2_status="approved"
                ).update(approval_2_status="stale")
            self.stdout.write(self.style.SUCCESS(f"Imported controller snapshot {revision}: {digest}"))
            return
        batch = SchedulePublicationBatch.objects.filter(pk=options["publication_batch"]).first()
        if not batch:
            raise CommandError("Publication batch does not exist.")
        validation = {}
        if options.get("validation_file"):
            validation_path = self._file(options["validation_file"])
            try:
                validation = json.loads(validation_path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CommandError("Validation evidence must be JSON.") from exc
        artifact_type = options["artifact_type"]
        if artifact_type == "nmg":
            validation = {**validation, **validate_nmg(path.read_bytes()), "status": "passed"}
        elif validation.get("status") != "passed":
            raise CommandError("BIN import requires validation evidence with status=passed.")
        with transaction.atomic():
            Device.objects.select_for_update().get(pk=batch.target_id)
            batch = SchedulePublicationBatch.objects.select_for_update().get(pk=batch.pk)
            revision = (batch.artifact_revisions.filter(artifact_type=artifact_type).aggregate(value=Max("revision"))["value"] or 0) + 1
            ArtifactRevision.objects.create(
                publication_batch=batch,
                artifact_type=artifact_type,
                revision=revision,
                file_reference=str(path),
                content_hash=digest,
                manifest={"size": path.stat().st_size, "sha256": digest},
                validation=validation,
            )
            if batch.approval_2_status == "approved":
                batch.approval_2_status = "stale"
                batch.notes = "\n".join(filter(None, (batch.notes, "Imported schedule artifact changed.")))
                batch.save(update_fields=["approval_2_status", "notes"])
        self.stdout.write(self.style.SUCCESS(f"Imported {artifact_type.upper()} artifact {revision}: {digest}"))
