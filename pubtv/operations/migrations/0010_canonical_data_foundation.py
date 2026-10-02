from django.db import migrations, models
import django.db.models.deletion


def backfill_producers(apps, schema_editor):
    Show = apps.get_model("operations", "Show")
    Producer = apps.get_model("operations", "Producer")
    Episode = apps.get_model("operations", "Episode")
    for show in Show.objects.all().order_by("pk"):
        if not any((show.legacy_producer_name, show.legacy_producer_phone, show.legacy_producer_email, show.legacy_producer_contact)):
            continue
        parts = (show.legacy_producer_name or "Unknown").strip().split(None, 1)
        first_name = parts[0][:80] or "Unknown"
        last_name = (parts[1] if len(parts) > 1 else "")[:80]
        existing = Producer.objects.filter(station_id=show.station_id, first_name=first_name, last_name=last_name, phone=show.legacy_producer_phone, email=show.legacy_producer_email).order_by("pk").first()
        if existing is None:
            existing = Producer.objects.create(
                station_id=show.station_id, first_name=first_name, last_name=last_name,
                phone=show.legacy_producer_phone, email=show.legacy_producer_email,
                legacy_contact=show.legacy_producer_contact,
            )
        if not show.primary_producer_id:
            Show.objects.filter(pk=show.pk).update(primary_producer_id=existing.pk)
        Episode.objects.filter(show_id=show.pk, producer_id__isnull=True).update(producer_id=existing.pk)


def reconcile_programming_duplicates(apps, schema_editor):
    Programming = apps.get_model("operations", "OccurrenceProgramming")
    AuditEvent = apps.get_model("operations", "AuditEvent")
    seen = {}
    for row in Programming.objects.all().order_by("occurrence_id", "device_id", "pk"):
        key = (row.occurrence_id, row.device_id)
        keeper = seen.get(key)
        if keeper is None:
            seen[key] = row
            continue
        # Preserve useful data on the deterministic lowest-pk row before the
        # duplicate is removed. This is migration-time data repair, not a
        # silent assertion that the two historical confirmations were equal.
        updates = {}
        for field in ("slot_assignment", "actor", "provenance", "note"):
            current = getattr(keeper, field, "")
            incoming = getattr(row, field, "")
            if not current and incoming:
                updates[field] = incoming
            elif current and incoming and current != incoming:
                AuditEvent.objects.create(
                    actor="migration", action="merge", entity="OccurrenceProgramming",
                    entity_id=row.pk, summary=f"Merged duplicate programming fact into {keeper.pk}; displaced {field} value preserved here: {incoming!r}.",
                )
        if getattr(row, "confirmed_at", None) and (not keeper.confirmed_at or row.confirmed_at < keeper.confirmed_at):
            updates["confirmed_at"] = row.confirmed_at
        if updates:
            Programming.objects.filter(pk=keeper.pk).update(**updates)
        AuditEvent.objects.create(
            actor="migration", action="merge", entity="OccurrenceProgramming",
            entity_id=row.pk, summary=f"Removed duplicate programming confirmation for occurrence {row.occurrence_id} and device {row.device_id}; canonical row {keeper.pk} retained.",
        )
        Programming.objects.filter(pk=row.pk).delete()


def project_legacy_preparation(apps, schema_editor):
    Preparation = apps.get_model("operations", "Preparation")
    AssetPreparation = apps.get_model("operations", "AssetPreparation")
    AssetTargetTransfer = apps.get_model("operations", "AssetTargetTransfer")
    Device = apps.get_model("operations", "Device")
    AuditEvent = apps.get_model("operations", "AuditEvent")
    for legacy in Preparation.objects.select_related("occurrence").order_by("pk"):
        occurrence = legacy.occurrence
        if not occurrence.asset_id:
            continue
        asset_prep, _ = AssetPreparation.objects.get_or_create(asset_id=occurrence.asset_id)
        prep_values = {
            "source_available": legacy.source_available,
            "source_available_at": legacy.source_available_at,
            "source_available_actor": legacy.source_available_actor,
            "source_available_provenance": legacy.source_available_provenance,
            "ame_preset": legacy.ame_preset,
            "ame_details": legacy.provenance,
            "encoded_at": legacy.ame_at,
            "encoded_actor": legacy.ame_actor,
            "encoded_provenance": legacy.ame_provenance,
        }
        prep_updates = {}
        for field, incoming in prep_values.items():
            if incoming in (None, "") or (field == "source_available" and incoming == "no" and not asset_prep.source_available_at):
                continue
            current = getattr(asset_prep, field, None)
            current_is_blank = current in (None, "") or (field == "source_available" and current == "no" and not asset_prep.source_available_at)
            if current_is_blank:
                prep_updates[field] = incoming
            elif current != incoming:
                AuditEvent.objects.create(
                    actor="migration", action="project", entity="Preparation", entity_id=legacy.pk,
                    summary=f"Conflicting legacy Preparation {legacy.pk} field {field}: displaced value {incoming!r}; canonical value {current!r} retained for asset {occurrence.asset_id}.",
                )
        if prep_updates:
            AssetPreparation.objects.filter(pk=asset_prep.pk).update(**prep_updates)

        ftp_name = (legacy.legacy_ftp_output_device or "").strip()
        library = (legacy.library_registration or "").strip()
        programming_devices = list(occurrence.programming.order_by("device_id").values_list("device_id", flat=True).distinct())
        device_id = None
        if ftp_name:
            device, _ = Device.objects.get_or_create(name=ftp_name)
            device_id = device.pk
        elif len(programming_devices) == 1:
            device_id = programming_devices[0]
        if not (ftp_name or library):
            continue
        if device_id is None:
            AuditEvent.objects.create(
                actor="migration", action="project", entity="Preparation", entity_id=legacy.pk,
                summary=f"Could not project legacy preparation transfer for asset {occurrence.asset_id}: no unambiguous target device; legacy transfer fields retained.",
            )
            continue
        transfer, _ = AssetTargetTransfer.objects.get_or_create(asset_id=occurrence.asset_id, device_id=device_id)
        transfer_values = {
            "ftp_details": ftp_name,
            "library_registration": library,
            "transferred_at": legacy.ftp_at or legacy.library_at,
            "transferred_actor": legacy.ftp_actor or legacy.library_actor,
            "transfer_provenance": legacy.ftp_provenance or legacy.library_provenance,
        }
        transfer_updates = {}
        for field, incoming in transfer_values.items():
            if incoming in (None, ""):
                continue
            current = getattr(transfer, field, None)
            if current in (None, ""):
                transfer_updates[field] = incoming
            elif current != incoming:
                AuditEvent.objects.create(
                    actor="migration", action="project", entity="Preparation", entity_id=legacy.pk,
                    summary=f"Conflicting legacy Preparation {legacy.pk} transfer field {field}: displaced value {incoming!r}; canonical value {current!r} retained for asset {occurrence.asset_id}, device {device_id}.",
                )
        if transfer_updates:
            AssetTargetTransfer.objects.filter(pk=transfer.pk).update(**transfer_updates)


class Migration(migrations.Migration):
    dependencies = [("operations", "0009_show_producer_metadata_and_labels")]

    operations = [
        migrations.RenameField(model_name="show", old_name="producer_name", new_name="legacy_producer_name"),
        migrations.RenameField(model_name="show", old_name="producer_phone", new_name="legacy_producer_phone"),
        migrations.RenameField(model_name="show", old_name="producer_email", new_name="legacy_producer_email"),
        migrations.RenameField(model_name="episode", old_name="received_at", new_name="legacy_received_at"),
        migrations.RenameField(model_name="mediaasset", old_name="label", new_name="legacy_label"),
        migrations.RenameField(model_name="preparation", old_name="ftp_output_device", new_name="legacy_ftp_output_device"),
        migrations.RenameField(model_name="preparation", old_name="uploaded_schedule_revision", new_name="legacy_uploaded_schedule_revision"),
        migrations.AddField(
            model_name="show", name="primary_producer",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="primary_shows", to="operations.producer"),
        ),
        migrations.AddField(model_name="occurrenceprogramming", name="slot_assignment", field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name="occurrenceprogramming", name="actor", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="occurrenceprogramming", name="provenance", field=models.TextField(blank=True)),
        migrations.CreateModel(
            name="AssetPreparation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_available", models.CharField(choices=[("yes", "Yes"), ("no", "No"), ("na", "N/A")], default="no", max_length=12)),
                ("source_available_at", models.DateTimeField(blank=True, null=True)),
                ("source_available_actor", models.CharField(blank=True, max_length=120)),
                ("source_available_provenance", models.TextField(blank=True)),
                ("ame_preset", models.CharField(blank=True, max_length=160)),
                ("ame_details", models.TextField(blank=True)),
                ("encoded_at", models.DateTimeField(blank=True, null=True)),
                ("encoded_actor", models.CharField(blank=True, max_length=120)),
                ("encoded_provenance", models.TextField(blank=True)),
                ("asset", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="preparation_record", to="operations.mediaasset")),
            ],
        ),
        migrations.CreateModel(
            name="AssetTargetTransfer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ftp_details", models.TextField(blank=True)),
                ("library_registration", models.CharField(blank=True, max_length=200)),
                ("transferred_at", models.DateTimeField(blank=True, null=True)),
                ("transferred_actor", models.CharField(blank=True, max_length=120)),
                ("transfer_provenance", models.TextField(blank=True)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="target_transfers", to="operations.mediaasset")),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="asset_transfers", to="operations.device")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("asset", "device"), name="unique_asset_target_transfer")]},
        ),
        migrations.RunPython(reconcile_programming_duplicates, migrations.RunPython.noop),
        migrations.RunPython(project_legacy_preparation, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="occurrenceprogramming",
            constraint=models.UniqueConstraint(fields=("occurrence", "device"), name="unique_occurrence_programming_device"),
        ),
        migrations.RunPython(backfill_producers, migrations.RunPython.noop),
    ]
