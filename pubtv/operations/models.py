from datetime import date, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.functional import cached_property
import uuid

def _duration_label(seconds):
    if seconds is None:
        return "Not recorded"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours: parts.append(f"{hours} hr")
    if minutes: parts.append(f"{minutes} min")
    if seconds: parts.append(f"{seconds} sec")
    return " ".join(parts) or "0 min"


def _clock_label(value):
    """Render owner-facing clock times in the shared ChannelDex style."""
    label = value.strftime("%I:%M %p").lstrip("0")
    return label.replace(" AM", " a.m.").replace(" PM", " p.m.")

class Station(models.Model):
    name = models.CharField(max_length=120, default="PUB-TV")
    timezone = models.CharField(max_length=64, default="America/Detroit")
    carry_forward_unassigned_episodes = models.BooleanField(default=False)
    # Controller schedule pulls are enabled by default; disabling this setting
    # requires an explicitly successful manual pull for every publication.
    auto_pull_controller_schedule = models.BooleanField(default=True)
    def __str__(self): return self.name

class Show(models.Model):
    DELIVERY_METHODS = [("google_drive", "Google Drive"), ("dropbox", "Dropbox"), ("email_link", "Email link"), ("smb_transfer", "SMB Transfer"), ("other", "Other")]
    SHOW_TYPES = [("arts_and_culture", "Arts and Culture"), ("religious", "Religious"), ("opinion", "Opinion"), ("public_affairs", "Public Affairs"), ("government", "Government"), ("education", "Education"), ("community", "Community"), ("entertainment", "Entertainment"), ("sports", "Sports"), ("other", "Other")]
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="shows")
    title = models.CharField(max_length=160)
    code = models.SlugField(max_length=40)
    show_type = models.CharField(max_length=24, choices=SHOW_TYPES, blank=True)
    show_type_other = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    legacy_producer_name = models.CharField(max_length=160, blank=True)
    legacy_producer_phone = models.CharField(max_length=80, blank=True)
    legacy_producer_email = models.EmailField(blank=True)
    legacy_producer_contact = models.CharField(max_length=240, blank=True)
    primary_producer = models.ForeignKey(
        "Producer", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="primary_shows",
    )
    website = models.URLField(blank=True)
    youtube = models.URLField(blank=True)
    facebook = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    primary_delivery_method = models.CharField(max_length=20, choices=DELIVERY_METHODS, blank=True)
    primary_delivery_other = models.CharField(max_length=160, blank=True)
    slot_duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["station", "code"], name="unique_station_show_code")]
    def __str__(self): return self.title
    def clean(self):
        super().clean()
        if self.primary_producer_id and self.primary_producer.station_id != self.station_id:
            raise ValidationError("Primary producer must belong to the same station as the show.")
        if self.primary_delivery_method == "other" and not self.primary_delivery_other.strip():
            raise ValidationError({"primary_delivery_other": "Describe the other delivery method."})
        if self.show_type == "other" and not self.show_type_other.strip():
            raise ValidationError({"show_type_other": "Describe the other show type."})

    @property
    def slot_duration_label(self):
        return _duration_label(self.slot_duration_seconds)

class Producer(models.Model):
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="producers")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    phone = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    legacy_contact = models.CharField(max_length=240, blank=True)
    external_membership_number = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["last_name", "first_name", "pk"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()

class Episode(models.Model):
    WORKFLOW_STAGE_ORDER = {
        "received": 10,
        "downloaded": 20,
        "encoded": 30,
        "transferred": 40,
        "programmed": 50,
        "scheduled": 60,
        "uploaded": 60,
        "aired": 70,
    }
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="episodes")
    title = models.CharField(max_length=160)
    producer = models.ForeignKey(
        Producer, null=True, blank=True, on_delete=models.PROTECT, related_name="episodes"
    )
    legacy_producer_reference = models.CharField(max_length=120, blank=True)
    intended_air_order = models.PositiveIntegerField(null=True, blank=True)
    intended_premiere_date = models.DateField(null=True, blank=True)
    runtime_seconds = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=[("pending", "Pending"), ("previously_scheduled", "Previously scheduled")], default="pending")
    legacy_received_at = models.DateTimeField(null=True, blank=True)
    def __str__(self): return f"{self.show.code}: {self.title}"

    def clean(self):
        super().clean()
        if self.producer_id and self.producer.station_id != self.show.station_id:
            raise ValidationError("Producer must belong to the same station as the episode's show.")

    @cached_property
    def latest_workflow_stage(self):
        return self.workflow_summary["label"]

    @cached_property
    def workflow_summary(self):
        """Concise status derived from the same canonical facts as the timeline."""
        events = self.workflow_timeline
        if not events:
            return {"label": "Not started", "stage": None, "fact_count": 0}
        _, latest = max(
            enumerate(events),
            key=lambda pair: (self.WORKFLOW_STAGE_ORDER.get(pair[1]["stage"], -1), pair[0]),
        )
        return {
            "label": latest["label"],
            "stage": latest["stage"],
            "fact_count": len(events),
        }

    @property
    def runtime_label(self):
        return _duration_label(self.runtime_seconds)

    @property
    def received_at(self):
        """Canonical receipt time, derived from linked deliveries when possible."""
        delivery_time = self.deliveries.filter(received_at__isnull=False).order_by("received_at").values_list("received_at", flat=True).first()
        return delivery_time or self.legacy_received_at

    @cached_property
    def workflow_timeline(self):
        """A derived, auditable readiness timeline including legacy assertions."""
        events = []
        for milestone in self.workflow_milestones.all():
            events.append({"stage": milestone.stage, "label": milestone.get_stage_display(), "at": milestone.completed_at, "actor": milestone.actor, "provenance": milestone.provenance, "legacy": True})
        for delivery in self.deliveries.filter(received_at__isnull=False):
            events.append({"stage": "received", "label": "Received", "at": delivery.received_at, "actor": "", "provenance": delivery.reference, "legacy": False})
        for asset in self.assets.all():
            prep = getattr(asset, "preparation_record", None)
            if prep:
                if prep.source_available in {"yes", "na"} or prep.source_available_at:
                    events.append({"stage": "received", "label": "Source available", "at": prep.source_available_at, "actor": prep.source_available_actor, "provenance": prep.source_available_provenance, "legacy": False})
                if prep.ame_at or prep.encoded_at or prep.ame_preset or prep.ame_details:
                    events.append({"stage": "encoded", "label": "Encoded", "at": prep.ame_at or prep.encoded_at, "actor": prep.ame_actor or prep.encoded_actor, "provenance": prep.ame_provenance or prep.encoded_provenance, "legacy": False})
            for transfer in asset.target_transfers.all():
                if transfer.transferred_at or transfer.ftp_details or transfer.library_registration:
                    events.append({"stage": "transferred", "label": "Transferred", "at": transfer.transferred_at, "actor": transfer.transferred_actor, "provenance": transfer.transfer_provenance, "legacy": False})
        for occurrence in self.occurrences.all():
            for programming in occurrence.programming.all():
                events.append({"stage": "programmed", "label": "Programmed", "at": programming.confirmed_at, "actor": programming.actor, "provenance": programming.provenance, "legacy": False})
            for coverage in UploadedOccurrenceCoverage.objects.filter(
                occurrence=occurrence,
                occurrence_revision=occurrence.revision,
                upload__state="active",
            ).select_related("upload__device"):
                events.append({"stage": "uploaded", "label": "Schedule uploaded", "at": coverage.upload.uploaded_at, "actor": "", "provenance": coverage.upload.device.name, "legacy": False})
            for evidence in occurrence.airing_evidence.all():
                events.append({"stage": "aired", "label": "Airing evidence recorded", "at": evidence.aired_at, "actor": evidence.actor, "provenance": evidence.source, "legacy": False})
        return sorted(events, key=lambda event: (event["at"] is None, event["at"]))

class EpisodeWorkflowMilestone(models.Model):
    STAGES = [
        ("received", "Received"),
        ("downloaded", "Downloaded"),
        ("encoded", "Encoded"),
        ("transferred", "Transferred"),
        ("scheduled", "Scheduled"),
    ]
    STAGE_ORDER = {value: index for index, (value, _) in enumerate(STAGES)}

    episode = models.ForeignKey(
        Episode, on_delete=models.CASCADE, related_name="workflow_milestones"
    )
    stage = models.CharField(max_length=16, choices=STAGES)
    completed_at = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=120, default="owner")
    provenance = models.CharField(max_length=200, default="manual")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["completed_at", "pk"]

    def clean(self):
        super().clean()
        if self.episode_id:
            later = self.episode.workflow_milestones.exclude(pk=self.pk).filter(
                stage__in=[
                    value
                    for value, index in self.STAGE_ORDER.items()
                    if index > self.STAGE_ORDER.get(self.stage, -1)
                ]
            )
            if later.exists():
                raise ValidationError(
                    "An earlier workflow stage cannot be recorded after a later stage. "
                    "Review the existing milestones first."
                )

class MediaAsset(models.Model):
    asset_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    episode = models.ForeignKey(Episode, null=True, blank=True, on_delete=models.SET_NULL, related_name="assets")
    legacy_label = models.CharField(max_length=160, blank=True)
    file_name = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=16, choices=[("source", "Source"), ("encoded", "Encoded")])
    version = models.CharField(max_length=80, default="v1")
    runtime_seconds = models.PositiveIntegerField(null=True, blank=True)
    smb_reference = models.CharField(max_length=300, blank=True)

    def __init__(self, *args, **kwargs):
        # Compatibility for old fixtures/callers; this never populates the
        # canonical filename or mirrors future changes.
        legacy_label = kwargs.pop("label", None)
        super().__init__(*args, **kwargs)
        # Seed the canonical filename for legacy Python callers once; later
        # edits remain deliberately non-mirroring.
        if legacy_label is not None and not self.file_name:
            self.file_name = legacy_label

    def save(self, *args, **kwargs):
        if not self.file_name and self.legacy_label:
            self.file_name = self.legacy_label
        return super().save(*args, **kwargs)

    @property
    def label(self):
        return self.file_name or self.legacy_label

    @label.setter
    def label(self, value):
        if not self.legacy_label and self.file_name and self.file_name != value:
            self.legacy_label = self.file_name
        self.file_name = value or ""

    def __str__(self):
        return self.file_name or self.legacy_label or f"Asset for {self.episode or 'unlinked episode'}"

    @property
    def runtime_label(self):
        return _duration_label(self.runtime_seconds)

class Delivery(models.Model):
    METHOD_CHOICES = Show.DELIVERY_METHODS
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    other_method = models.CharField(max_length=160, blank=True)
    reference = models.CharField(max_length=300)
    notified_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    episodes = models.ManyToManyField(Episode, blank=True, related_name="deliveries")
    def __str__(self):
        return self.reference or self.get_method_display()
    def clean(self):
        super().clean()
        if self.method == "other" and not self.other_method.strip():
            raise ValidationError({"other_method": "Describe the other delivery method."})

class Device(models.Model):
    name = models.CharField(max_length=120, unique=True)

    def __str__(self):
        return self.name

class RecurrenceSlot(models.Model):
    WEEKDAYS = list(enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="slots")
    show = models.ForeignKey(Show, null=True, blank=True, on_delete=models.CASCADE, related_name="slots")
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    duration_seconds = models.PositiveIntegerField()
    is_premiere = models.BooleanField(default=False)
    active_from = models.DateField(null=True, blank=True)
    active_until = models.DateField(null=True, blank=True)
    def __str__(self):
        show = self.show.title if self.show else "Unassigned show"
        start = _clock_label(self.start_time) if hasattr(self.start_time, "strftime") else str(self.start_time)
        return f"{show} · {self.get_weekday_display()} {start}"
    def clean(self):
        if self.show_id and self.show and self.station_id != self.show.station_id:
            raise ValidationError("Recurrence slot show must belong to its station.")
        if self.active_from and self.active_until and self.active_from > self.active_until:
            raise ValidationError("Recurrence slot active dates are reversed.")
        if self.is_premiere and self.show_id:
            start = self.active_from or date.min
            end = self.active_until or date.max
            overlapping = RecurrenceSlot.objects.filter(show_id=self.show_id, is_premiere=True).exclude(pk=self.pk)
            overlapping = overlapping.filter(
                models.Q(active_until__isnull=True) | models.Q(active_until__gte=start),
                models.Q(active_from__isnull=True) | models.Q(active_from__lte=end),
            )
            if overlapping.exists():
                raise ValidationError(
                    "This show already has a premiere slot active during these effective dates."
                )

    def save(self, *args, **kwargs):
        if self.show_id and not self.pk and not self.is_premiere:
            self.is_premiere = not RecurrenceSlot.objects.filter(show_id=self.show_id).exists()
        if self.show_id and self.show.slot_duration_seconds:
            self.duration_seconds = self.show.slot_duration_seconds
        super().save(*args, **kwargs)

    @property
    def duration_label(self):
        return _duration_label(self.duration_seconds)

class WeeklyEpisodeAssignment(models.Model):
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="weekly_assignments")
    week_start = models.DateField()
    premiere_date = models.DateField(null=True, blank=True)
    episode = models.ForeignKey(Episode, null=True, blank=True, on_delete=models.SET_NULL)
    selection_type = models.CharField(max_length=16, choices=[("premiere", "New premiere"), ("rerun", "Selected older rerun"), ("none", "No program")])
    is_automatic_carry_forward = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["show", "week_start"], name="unique_show_week")]
    def __str__(self):
        return f"{self.show.title} · week of {self.week_start:%Y-%m-%d}"
    def clean(self):
        if self.week_start and self.week_start.weekday() != 0:
            raise ValidationError("Week start must be a Monday.")
        if self.episode_id and self.episode and self.episode.show_id != self.show_id:
            raise ValidationError("Assigned episode must belong to the assigned show.")
        if self.selection_type == "none" and self.episode_id:
            raise ValidationError("A no-program assignment cannot include an episode.")
        if self.selection_type in {"premiere", "rerun"} and not self.episode_id:
            raise ValidationError("A premiere or rerun assignment requires an episode.")
        if self.premiere_date and self.show_id:
            premiere_slots = self.show.slots.filter(is_premiere=True)
            matching = [
                slot for slot in premiere_slots
                if slot.weekday == self.premiere_date.weekday()
                and (not slot.active_from or slot.active_from <= self.premiere_date)
                and (not slot.active_until or slot.active_until >= self.premiere_date)
            ]
            if len(matching) != 1:
                raise ValidationError({"premiere_date": "Choose a date matching this show's active premiere time."})

class Occurrence(models.Model):
    TYPES = [(x, x.replace("_", " ").title()) for x in ("episode", "media", "station_id", "psa", "filler", "live")]
    STATUS = [(x, x.title()) for x in ("planned", "cancelled", "preempted", "rescheduled")]
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="occurrences")
    show = models.ForeignKey(Show, null=True, blank=True, on_delete=models.SET_NULL, related_name="occurrences")
    episode = models.ForeignKey(Episode, null=True, blank=True, on_delete=models.SET_NULL, related_name="occurrences")
    asset = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL)
    item_type = models.CharField(max_length=16, choices=TYPES)
    label = models.CharField(max_length=160)
    starts_at = models.DateTimeField()
    planned_duration_seconds = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=STATUS, default="planned")
    reason = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)
    weekly_assignment = models.ForeignKey("WeeklyEpisodeAssignment", null=True, blank=True, on_delete=models.PROTECT, related_name="occurrences")
    recurrence_slot = models.ForeignKey(
        RecurrenceSlot, null=True, blank=True, on_delete=models.SET_NULL, related_name="occurrences"
    )
    schedule_role = models.CharField(
        max_length=16,
        choices=[("manual", "Manual schedule item"), ("premiere", "New premiere"), ("replay", "Replay"), ("rerun", "Selected older episode")],
        default="manual",
    )
    def __str__(self):
        show_title = self.show.title if self.show else None
        episode_title = self.episode.title if self.episode else None
        item = " · ".join(part for part in (show_title, episode_title) if part) or self.label
        starts_at = self.starts_at
        if hasattr(starts_at, "strftime"):
            if timezone.is_aware(starts_at):
                timezone_name = getattr(self.station, "timezone", "America/Detroit") if self.station_id else "America/Detroit"
                try:
                    starts_at = timezone.localtime(starts_at, ZoneInfo(timezone_name))
                except (ZoneInfoNotFoundError, ValueError):
                    starts_at = timezone.localtime(starts_at, ZoneInfo("America/Detroit"))
            time_label = f"{starts_at:%Y-%m-%d} {_clock_label(starts_at)}"
        else:
            time_label = str(starts_at)
        return f"{item} · {time_label}"
    def clean(self):
        if self.item_type == "episode" and not self.episode: raise ValidationError("Episode items require an episode.")
        if self.item_type == "live" and self.asset: raise ValidationError("Live items cannot have a media asset.")
        if self.show and self.show.station_id != self.station_id: raise ValidationError("Show must belong to occurrence station.")
        if self.episode and (self.episode.show.station_id != self.station_id or (self.show_id and self.episode.show_id != self.show_id)):
            raise ValidationError("Episode must belong to occurrence station and show.")
        if self.asset and self.asset.episode_id and self.episode_id and self.asset.episode_id != self.episode_id:
            raise ValidationError("Asset must belong to occurrence episode.")
    @property
    def ends_at(self): return self.starts_at + timedelta(seconds=self.planned_duration_seconds)
    @property
    def over_slot(self):
        return bool(self.episode and self.episode.runtime_seconds and self.episode.runtime_seconds > self.planned_duration_seconds)
    def save(self, *args, **kwargs):
        skip_revision = kwargs.pop("skip_revision", False)
        if self.pk and not skip_revision:
            self.revision = models.F("revision") + 1
        super().save(*args, **kwargs)

    @property
    def schedule_context(self):
        if self.weekly_assignment_id and self.weekly_assignment.is_automatic_carry_forward:
            return "Automatic carry-forward replay"
        if self.schedule_role != "manual":
            return self.get_schedule_role_display()
        if not self.weekly_assignment_id:
            return "Manual schedule item"
        if self.weekly_assignment.selection_type == "rerun":
            return "Selected older episode"
        if self.weekly_assignment.selection_type == "none":
            return "No program"
        if not self.recurrence_slot:
            return self.weekly_assignment.get_selection_type_display()
        return "New premiere" if self.recurrence_slot and self.recurrence_slot.is_premiere else "Replay"

    @property
    def duration_label(self):
        return _duration_label(self.planned_duration_seconds)

    @property
    def actual_runtime_seconds(self):
        """Return recorded media runtime, never the reserved slot duration."""
        if self.episode and self.episode.runtime_seconds is not None:
            return self.episode.runtime_seconds
        if self.asset and self.asset.runtime_seconds is not None:
            return self.asset.runtime_seconds
        return None

    @property
    def actual_runtime_label(self):
        return _duration_label(self.actual_runtime_seconds)

    @property
    def unique_asset_id_label(self):
        return str(self.asset.asset_id) if self.asset else "Not assigned"

    @property
    def display_label(self):
        if self.item_type == "episode" and self.episode:
            return " · ".join(part for part in (self.episode.show.title, self.episode.title) if part)
        return self.label

class Preparation(models.Model):
    occurrence = models.OneToOneField(Occurrence, on_delete=models.CASCADE, related_name="preparation")
    source_available = models.CharField(max_length=12, choices=[("yes", "Yes"), ("no", "No"), ("na", "N/A")], default="no")
    ame_preset = models.CharField(max_length=160, blank=True)
    legacy_ftp_output_device = models.CharField(max_length=200, blank=True)
    library_registration = models.CharField(max_length=200, blank=True)
    slot_assignment = models.CharField(max_length=200, blank=True)
    legacy_uploaded_schedule_revision = models.CharField(max_length=120, blank=True)
    provenance = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)
    source_available_actor = models.CharField(max_length=120, blank=True); source_available_at = models.DateTimeField(null=True, blank=True); source_available_provenance = models.TextField(blank=True)
    ame_actor = models.CharField(max_length=120, blank=True); ame_at = models.DateTimeField(null=True, blank=True); ame_provenance = models.TextField(blank=True)
    ftp_actor = models.CharField(max_length=120, blank=True); ftp_at = models.DateTimeField(null=True, blank=True); ftp_provenance = models.TextField(blank=True)
    library_actor = models.CharField(max_length=120, blank=True); library_at = models.DateTimeField(null=True, blank=True); library_provenance = models.TextField(blank=True)
    slot_actor = models.CharField(max_length=120, blank=True); slot_at = models.DateTimeField(null=True, blank=True); slot_provenance = models.TextField(blank=True)
    upload_actor = models.CharField(max_length=120, blank=True); upload_at = models.DateTimeField(null=True, blank=True); upload_provenance = models.TextField(blank=True)

    def __init__(self, *args, **kwargs):
        ftp_device = kwargs.pop("ftp_output_device", None)
        upload_revision = kwargs.pop("uploaded_schedule_revision", None)
        super().__init__(*args, **kwargs)
        if ftp_device is not None and not self.legacy_ftp_output_device:
            self.legacy_ftp_output_device = ftp_device
        if upload_revision is not None and not self.legacy_uploaded_schedule_revision:
            self.legacy_uploaded_schedule_revision = upload_revision

    @property
    def ftp_output_device(self):
        return self.legacy_ftp_output_device

    @ftp_output_device.setter
    def ftp_output_device(self, value):
        self.legacy_ftp_output_device = value or ""

    @property
    def uploaded_schedule_revision(self):
        return self.legacy_uploaded_schedule_revision

    @uploaded_schedule_revision.setter
    def uploaded_schedule_revision(self, value):
        self.legacy_uploaded_schedule_revision = value or ""


class AssetPreparation(models.Model):
    SOURCE_CHOICES = [("yes", "Yes"), ("no", "No"), ("na", "N/A")]
    asset = models.OneToOneField(MediaAsset, on_delete=models.CASCADE, related_name="preparation_record")
    source_available = models.CharField(max_length=12, choices=SOURCE_CHOICES, default="no")
    source_available_at = models.DateTimeField(null=True, blank=True)
    source_available_actor = models.CharField(max_length=120, blank=True)
    source_available_provenance = models.TextField(blank=True)
    ame_preset = models.CharField(max_length=160, blank=True)
    ame_details = models.TextField(blank=True)
    ame_at = models.DateTimeField(null=True, blank=True)
    ame_actor = models.CharField(max_length=120, blank=True)
    ame_provenance = models.TextField(blank=True)
    encoded_at = models.DateTimeField(null=True, blank=True)
    encoded_actor = models.CharField(max_length=120, blank=True)
    encoded_provenance = models.TextField(blank=True)

    def clean(self):
        super().clean()
        if self.source_available == "yes" and not self.source_available_at:
            raise ValidationError({"source_available_at": "Record when the source became available."})
        if self.ame_details and not self.ame_preset:
            raise ValidationError({"ame_preset": "An AME preset is required when AME details are recorded."})


class AssetTargetTransfer(models.Model):
    asset = models.ForeignKey(MediaAsset, on_delete=models.CASCADE, related_name="target_transfers")
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="asset_transfers")
    ftp_details = models.TextField(blank=True)
    library_registration = models.CharField(max_length=200, blank=True)
    transferred_at = models.DateTimeField(null=True, blank=True)
    transferred_actor = models.CharField(max_length=120, blank=True)
    transfer_provenance = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["asset", "device"], name="unique_asset_target_transfer")]

    def clean(self):
        super().clean()
        if not self.ftp_details and not self.library_registration:
            raise ValidationError("Record FTP transfer details or library registration.")

class UploadedScheduleRevision(models.Model):
    STATE = [(x, x.title()) for x in ("active", "superseded", "invalidated")]
    device = models.ForeignKey(Device, on_delete=models.PROTECT)
    external_reference = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    state = models.CharField(max_length=16, choices=STATE, default="active")
    occurrences = models.ManyToManyField(Occurrence, through="UploadedOccurrenceCoverage", blank=True, related_name="uploaded_revisions")
    supersession_reason = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)
    def __str__(self):
        reference = self.external_reference or "Uploaded schedule"
        return f"{reference} · {self.uploaded_at:%Y-%m-%d} {_clock_label(self.uploaded_at)}"

class UploadedOccurrenceCoverage(models.Model):
    upload = models.ForeignKey(UploadedScheduleRevision, on_delete=models.CASCADE)
    occurrence = models.ForeignKey(Occurrence, on_delete=models.PROTECT)
    occurrence_revision = models.PositiveIntegerField(default=1)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["upload", "occurrence"], name="unique_upload_occurrence")]
    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Uploaded occurrence coverage is immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValidationError("Uploaded occurrence coverage is immutable.")

class OccurrenceProgramming(models.Model):
    occurrence = models.ForeignKey(Occurrence, on_delete=models.CASCADE, related_name="programming")
    device = models.ForeignKey(Device, on_delete=models.PROTECT)
    slot_assignment = models.CharField(max_length=200, blank=True)
    confirmed_at = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=120, blank=True)
    provenance = models.TextField(blank=True)
    note = models.TextField(blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["occurrence", "device"], name="unique_occurrence_programming_device")]
    def __str__(self):
        return f"{self.occurrence} · {self.device}"

class AiringEvidence(models.Model):
    occurrence = models.ForeignKey(Occurrence, null=True, blank=True, on_delete=models.SET_NULL, related_name="airing_evidence")
    status = models.CharField(max_length=20, choices=[("reported", "Reported/observed"), ("log_verified", "Log verified")])
    source = models.CharField(max_length=200)
    aired_at = models.DateTimeField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=120, blank=True)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrections")
    def __str__(self):
        item = self.occurrence or "Unlinked occurrence"
        return f"{item} · {self.get_status_display()} · {self.aired_at:%Y-%m-%d} {_clock_label(self.aired_at)}"

class AuditEvent(models.Model):
    actor = models.CharField(max_length=120, default="owner")
    occurred_at = models.DateTimeField(default=timezone.now)
    action = models.CharField(max_length=40)
    entity = models.CharField(max_length=80)
    entity_id = models.PositiveIntegerField(null=True, blank=True)
    revision = models.PositiveIntegerField(null=True, blank=True)
    summary = models.TextField()


# Durable UltraNEXUS integration records.  These models deliberately store
# references, hashes, and operator-entered evidence; they never store raw
# passwords, tokens, or other credentials.
class UltraNexusTargetSettings(models.Model):
    QUALIFICATION_STATUS = [(x, x.replace("_", " ").title()) for x in ("open", "passed", "blocked")]
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="ultranexus_settings")
    version = models.PositiveIntegerField(default=1)
    settings = models.JSONField(default=dict)
    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(null=True, blank=True)
    media_directory = models.CharField(max_length=500, blank=True)
    schedule_path = models.CharField(max_length=500, blank=True)
    secret_reference = models.CharField(max_length=255, blank=True)
    reconciliation_mode = models.CharField(max_length=16, choices=[("preserve", "Preserve"), ("authoritative", "Authoritative")], default="preserve")
    capability_flags = models.JSONField(default=dict, blank=True)
    base_nmg_path = models.CharField(max_length=500, blank=True)
    base_nmg_hash = models.CharField(max_length=128, blank=True)
    base_bin_path = models.CharField(max_length=500, blank=True)
    base_bin_hash = models.CharField(max_length=64, blank=True)
    command_port = models.PositiveIntegerField(default=23)
    command_username = models.CharField(max_length=120, blank=True)
    command_secret_reference = models.CharField(max_length=255, blank=True)
    controller_family = models.CharField(max_length=64, blank=True)
    firmware_version = models.CharField(max_length=32, blank=True)
    output_number = models.PositiveIntegerField(null=True, blank=True)
    media_profile = models.CharField(max_length=64, blank=True)
    profile_identity_hash = models.CharField(max_length=64, blank=True)
    ame_preset_sha256 = models.CharField(max_length=64, blank=True)
    ffmpeg_profile_sha256 = models.CharField(max_length=64, blank=True)
    nmg_template_sha256 = models.CharField(max_length=64, blank=True)
    bin_template_sha256 = models.CharField(max_length=64, blank=True)
    qualification_status = models.CharField(max_length=16, choices=QUALIFICATION_STATUS, default="open")
    qualification_evidence_hash = models.CharField(max_length=64, blank=True)
    settings_hash = models.CharField(max_length=128, blank=True)
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.CharField(max_length=120, default="owner")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["target", "version"], name="unique_ultranexus_target_settings_version"),
            models.UniqueConstraint(fields=["target"], condition=Q(is_current=True), name="unique_current_ultranexus_target_settings"),
        ]

    def clean(self):
        super().clean()
        if any(word in str(self.settings).lower() for word in ("password", "passwd", "token", "secret", "credential")):
            raise ValidationError("Target settings must not contain raw credentials.")
        if self.port is not None and not 1 <= self.port <= 65535:
            raise ValidationError({"port": "Port must be between 1 and 65535."})
        if self.secret_reference and (":" not in self.secret_reference or any(ch.isspace() for ch in self.secret_reference)):
            raise ValidationError({"secret_reference": "Secret reference must be an opaque service:account reference."})
        if self.base_nmg_hash and (len(self.base_nmg_hash) not in (32, 40, 64, 96, 128) or any(ch not in "0123456789abcdefABCDEF" for ch in self.base_nmg_hash)):
            raise ValidationError({"base_nmg_hash": "Base NMG hash must be a standard hexadecimal digest length."})
        if self.base_bin_hash and (len(self.base_bin_hash) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in self.base_bin_hash)):
            raise ValidationError({"base_bin_hash": "Base BIN hash must be SHA-256."})
        if not 1 <= self.command_port <= 65535:
            raise ValidationError({"command_port": "Command port must be between 1 and 65535."})
        if self.schedule_path and self.schedule_path != "/internal/schedule/schedule.bin":
            raise ValidationError({"schedule_path": "The qualified schedule path is /internal/schedule/schedule.bin."})
        if self.command_secret_reference and (":" not in self.command_secret_reference or any(ch.isspace() for ch in self.command_secret_reference)):
            raise ValidationError({"command_secret_reference": "Use an opaque Keychain service:account reference."})
        hash_fields = ("profile_identity_hash", "ame_preset_sha256", "ffmpeg_profile_sha256",
                       "nmg_template_sha256", "bin_template_sha256",
                       "qualification_evidence_hash")
        for field in hash_fields:
            value = getattr(self, field)
            if value and (len(value) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in value)):
                raise ValidationError({field: "Use an exact SHA-256 hexadecimal digest."})
        if self.qualification_status == "passed" and not self.qualification_evidence_hash:
            raise ValidationError({"qualification_evidence_hash": "Passed qualification requires exact evidence SHA-256."})


# Alternate spelling retained for callers that render the product name as an
# all-caps acronym.
UltraNEXUSTargetSettings = UltraNexusTargetSettings


class MediaInspection(models.Model):
    STATUS = [(x, x.replace("_", " ").title()) for x in ("passed", "failed", "needs_review")]
    asset = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="ultranexus_inspections")
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="media_inspections")
    local_path = models.CharField(max_length=500)
    file_size = models.PositiveBigIntegerField(default=0)
    probe_json = models.JSONField(default=dict, blank=True)
    tool = models.CharField(max_length=120, blank=True)
    tool_version = models.CharField(max_length=80, blank=True)
    profile = models.CharField(max_length=120, blank=True)
    inspected_at = models.DateTimeField(default=timezone.now)
    inspector = models.CharField(max_length=120, default="owner")
    status = models.CharField(max_length=20, choices=STATUS)
    media_hash = models.CharField(max_length=128, blank=True)
    details = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    def clean(self):
        super().clean()
        if self.status == "passed" and not self.media_hash:
            raise ValidationError({"media_hash": "A passed inspection requires a media hash."})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Media inspections are immutable; record a new inspection.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Media inspections are immutable.")


class MediaIdAllocation(models.Model):
    """Permanent target-scoped reservation, retained after a binding changes."""
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="media_id_allocations")
    media_id_uint16 = models.PositiveIntegerField(null=True, blank=True)
    resource_reference_uint32 = models.PositiveBigIntegerField(null=True, blank=True)
    casefold_key = models.CharField(max_length=255)
    media_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["target", "media_id_uint16"], condition=Q(media_id_uint16__isnull=False), name="unique_target_allocated_media_id"),
            models.UniqueConstraint(fields=["target", "resource_reference_uint32"], condition=Q(resource_reference_uint32__isnull=False), name="unique_target_allocated_resource_ref"),
            models.UniqueConstraint(fields=["target", "casefold_key"], name="unique_target_allocated_media_name"),
        ]

    def clean(self):
        super().clean()
        if self.media_id_uint16 is not None and not 1 <= self.media_id_uint16 <= 65535:
            raise ValidationError("Allocated Media ID must be nonzero uint16.")
        if self.resource_reference_uint32 is not None and not 1 <= self.resource_reference_uint32 <= 4294967295:
            raise ValidationError("Allocated resource reference must be nonzero uint32.")
        if not self.casefold_key:
            raise ValidationError("Allocated filename key is required.")


class MediaBinding(models.Model):
    BINDING_TYPES = [(x, x.title()) for x in ("source", "encoded", "controller")]
    asset = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="ultranexus_bindings")
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="media_bindings")
    binding_type = models.CharField(max_length=16, choices=BINDING_TYPES, default="encoded")
    media_id_uint16 = models.PositiveIntegerField(null=True, blank=True)
    resource_reference_uint32 = models.PositiveBigIntegerField(null=True, blank=True)
    bare_filename = models.CharField(max_length=255, blank=True)
    casefold_key = models.CharField(max_length=255, blank=True)
    profile = models.CharField(max_length=120, blank=True)
    verification_basis = models.CharField(max_length=20, choices=[("legacy", "Legacy"), ("locally_verified", "Locally verified")], default="legacy")
    local_inspection = models.ForeignKey(MediaInspection, null=True, blank=True, on_delete=models.PROTECT, related_name="bindings")
    external_reference = models.CharField(max_length=240, blank=True)
    legacy_attestation_hash = models.CharField(max_length=128, blank=True)
    legacy_attested_at = models.DateTimeField(null=True, blank=True)
    legacy_attested_by = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["asset", "target", "binding_type"], name="unique_ultranexus_media_binding"),
            models.UniqueConstraint(fields=["target", "media_id_uint16"], condition=Q(media_id_uint16__isnull=False), name="unique_target_media_id_uint16"),
            models.UniqueConstraint(fields=["target", "resource_reference_uint32"], condition=Q(resource_reference_uint32__isnull=False), name="unique_target_resource_reference_uint32"),
            models.UniqueConstraint(fields=["target", "casefold_key"], condition=~Q(casefold_key=""), name="unique_target_casefold_key"),
        ]

    def clean(self):
        super().clean()
        if self.media_id_uint16 is not None and self.media_id_uint16 > 65535:
            raise ValidationError({"media_id_uint16": "Media ID must fit uint16."})
        if self.resource_reference_uint32 is not None and self.resource_reference_uint32 > 4294967295:
            raise ValidationError({"resource_reference_uint32": "Resource reference must fit uint32."})
        if self.verification_basis == "locally_verified" and not self.local_inspection_id:
            raise ValidationError("Locally verified bindings require a local inspection.")
        if self.bare_filename and not self.casefold_key:
            self.casefold_key = self.bare_filename.casefold()
        if self.verification_basis == "locally_verified":
            inspection = self.local_inspection
            if not inspection or inspection.asset_id != self.asset_id or inspection.target_id != self.target_id or inspection.status != "passed":
                raise ValidationError("Locally verified bindings require a passed inspection for the same asset and target.")
        if self.verification_basis == "legacy" and any((self.legacy_attestation_hash, self.legacy_attested_at, self.legacy_attested_by)):
            if not all((self.legacy_attestation_hash, self.legacy_attested_at, self.legacy_attested_by, self.external_reference)):
                raise ValidationError("Legacy attestation requires its evidence hash, time, actor, and controller reference.")

    @property
    def is_schedule_ready(self):
        if self.verification_basis == "locally_verified":
            return bool(self.local_inspection_id and self.local_inspection.status == "passed")
        return bool(self.legacy_attestation_hash and self.legacy_attested_at and self.legacy_attested_by and self.external_reference)

    def save(self, *args, **kwargs):
        if self.bare_filename and not self.casefold_key:
            self.casefold_key = self.bare_filename.casefold()
        return super().save(*args, **kwargs)


class PreparationBatch(models.Model):
    STATUS = [(x, x.replace("_", " ").title()) for x in ("draft", "approved", "in_progress", "complete", "blocked", "failed", "cancelled")]
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="preparation_batches")
    show = models.ForeignKey(Show, null=True, blank=True, on_delete=models.PROTECT, related_name="preparation_batches")
    submission_token = models.UUIDField(null=True, blank=True, unique=True)
    label = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="draft")
    removed_from_list = models.BooleanField(default=False)
    approval_1_snapshot = models.JSONField(default=dict, blank=True)
    approval_1_hash = models.CharField(max_length=128, blank=True)
    approval_1_status = models.CharField(max_length=16, choices=[("pending", "Pending"), ("approved", "Approved"), ("stale", "Stale")], default="pending")
    approval_1_at = models.DateTimeField(null=True, blank=True)
    approval_1_actor = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.CharField(max_length=120, default="owner")
    notes = models.TextField(blank=True)

    def clean(self):
        super().clean()
        if self.approval_1_status == "approved" and not (self.approval_1_snapshot and self.approval_1_hash and self.approval_1_at):
            raise ValidationError("Approval 1 requires a snapshot, hash, and approval time.")


class MediaIntakeReview(models.Model):
    """Durable review context for the connected intake flow.

    The review stores only owner-selected identifiers and display metadata;
    private source paths remain on preparation items and are never exposed in
    signed planner or schedule URLs.
    """
    STATUS = [("pending", "Pending"), ("confirmed", "Confirmed"), ("cancelled", "Cancelled")]
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="media_intake_reviews")
    show = models.ForeignKey(Show, on_delete=models.PROTECT, related_name="media_intake_reviews")
    episode = models.ForeignKey(Episode, null=True, blank=True, on_delete=models.PROTECT, related_name="media_intake_reviews")
    asset = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.PROTECT, related_name="media_intake_reviews")
    order = models.PositiveIntegerField(default=0)
    action = models.CharField(max_length=32, default="prepare_only")
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="pending")
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.CharField(max_length=120, default="owner")

    class Meta:
        ordering = ["order", "pk"]


class PreparationBatchItem(models.Model):
    APPROVAL_STATUS = [(x, x.replace("_", " ").title()) for x in ("pending", "approved", "rejected", "stale")]
    batch = models.ForeignKey(PreparationBatch, on_delete=models.CASCADE, related_name="items")
    asset = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="preparation_batch_items")
    occurrence = models.ForeignKey(Occurrence, null=True, blank=True, on_delete=models.PROTECT, related_name="preparation_batch_items")
    selected_input_path = models.CharField(max_length=500, blank=True)
    selected_input_hash = models.CharField(max_length=128, blank=True)
    resulting_inspection = models.ForeignKey(MediaInspection, null=True, blank=True, on_delete=models.PROTECT, related_name="resulting_batch_items")
    resulting_binding = models.ForeignKey(MediaBinding, null=True, blank=True, on_delete=models.PROTECT, related_name="resulting_batch_items")
    execution_status = models.CharField(max_length=20, choices=[("pending", "Queued"), ("encoding", "Encoding"), ("validating", "Validating"), ("transferring", "Transferring"), ("verifying", "Verifying"), ("ready", "Ready"), ("blocked", "Needs attention"), ("failed", "Needs attention")], default="pending")
    blocker = models.TextField(blank=True)
    encode_before_transfer = models.BooleanField(default=True)
    approval_1_snapshot = models.JSONField(default=dict, blank=True)
    approval_1_hash = models.CharField(max_length=128, blank=True)
    approval_1_status = models.CharField(max_length=16, choices=APPROVAL_STATUS, default="pending")
    approval_1_at = models.DateTimeField(null=True, blank=True)
    approval_1_actor = models.CharField(max_length=120, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "asset"], name="unique_preparation_batch_asset")]
        ordering = ["position", "pk"]

    @property
    def approval_1_snapshot_stale(self):
        return self.approval_1_status == "stale"

    @property
    def is_approval_1_stale(self):
        return self.approval_1_snapshot_stale

    def clean(self):
        super().clean()
        if self.approval_1_status == "approved" and not (self.approval_1_snapshot and self.approval_1_hash and self.approval_1_at):
            raise ValidationError("Approval 1 requires a snapshot, hash, and approval time.")


class TransferAttempt(models.Model):
    STATUS = [(x, x.replace("_", " ").title()) for x in ("planned", "started", "succeeded", "failed", "cancelled")]
    item = models.ForeignKey(PreparationBatchItem, on_delete=models.PROTECT, related_name="transfer_attempts")
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="transfer_attempts")
    attempted_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=STATUS, default="planned")
    external_reference = models.CharField(max_length=240, blank=True)
    artifact_hash = models.CharField(max_length=128, blank=True)
    operator = models.CharField(max_length=120, default="owner")
    evidence = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)


class PreparationJob(models.Model):
    STATUS = [(x, x.replace("_", " ").title()) for x in ("queued", "running", "succeeded", "failed", "cancelled")]
    batch = models.ForeignKey(PreparationBatch, on_delete=models.CASCADE, related_name="jobs")
    status = models.CharField(max_length=16, choices=STATUS, default="queued")
    idempotency_key = models.CharField(max_length=160, unique=True)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)


class ControllerSnapshot(models.Model):
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="controller_snapshots")
    captured_at = models.DateTimeField(default=timezone.now)
    revision = models.PositiveIntegerField(default=1)
    snapshot_hash = models.CharField(max_length=128)
    payload = models.JSONField(default=dict)
    source_reference = models.CharField(max_length=240, blank=True)
    trigger = models.CharField(max_length=16, choices=[("automatic", "Automatic"), ("manual", "Manual")], default="manual")
    actor = models.CharField(max_length=120, default="owner")
    settings_revision = models.PositiveIntegerField(null=True, blank=True)
    settings_hash = models.CharField(max_length=128, blank=True)
    target_path = models.CharField(max_length=500, default="/internal/schedule/schedule.bin")
    source_bytes = models.PositiveBigIntegerField(default=0)
    qualification = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target", "revision"], name="unique_controller_snapshot_revision")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Controller snapshots are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Controller snapshots are immutable.")


class SchedulePublicationBatch(models.Model):
    review_token = models.UUIDField(null=True, blank=True, unique=True)
    preparation_item = models.OneToOneField("PreparationBatchItem", null=True, blank=True, on_delete=models.PROTECT, related_name="schedule_publication",)
    RECONCILIATION_MODES = [("preserve", "Preserve"), ("authoritative", "Authoritative")]
    WORKFLOW_MODES = [("selected_changes", "Selected changes"), ("full_week", "Full week")]
    STATUS = [(x, x.replace("_", " ").title()) for x in ("previewed", "approved", "awaiting_activation", "delivering", "verified", "blocked", "failed", "cancelled")]
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="schedule_publication_batches")
    workflow_mode = models.CharField(max_length=20, choices=WORKFLOW_MODES, default="selected_changes")
    reconciliation_mode = models.CharField(max_length=16, choices=RECONCILIATION_MODES, default="preserve")
    status = models.CharField(max_length=24, choices=STATUS, default="previewed")
    approval_2_snapshot = models.JSONField(default=dict, blank=True)
    approval_2_hash = models.CharField(max_length=128, blank=True)
    approval_2_status = models.CharField(max_length=16, choices=[("pending", "Pending"), ("approved", "Approved"), ("stale", "Stale")], default="pending")
    approval_2_at = models.DateTimeField(null=True, blank=True)
    approval_2_actor = models.CharField(max_length=120, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    requested_activation_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.CharField(max_length=120, default="owner")
    notes = models.TextField(blank=True)
    controller_snapshot = models.ForeignKey(ControllerSnapshot, null=True, blank=True, on_delete=models.PROTECT, related_name="publication_batches")
    controller_snapshot_hash = models.CharField(max_length=128, blank=True)
    mutation_plan = models.JSONField(default=dict, blank=True)
    mutation_plan_hash = models.CharField(max_length=128, blank=True)
    review_diff = models.JSONField(default=dict, blank=True)
    preparation_kind = models.CharField(max_length=24, default="publication")

    def clean(self):
        super().clean()
        if self.workflow_mode == "full_week" and self.reconciliation_mode != "authoritative":
            raise ValidationError("Full-week publication requires authoritative reconciliation because ChannelDex owns the week.")
        if self.approval_2_status == "approved" and not (self.approval_2_snapshot and self.approval_2_hash and self.approval_2_at):
            raise ValidationError("Approval 2 requires a snapshot, hash, and approval time.")
        if self.status == "verified" and not self.activated_at:
            raise ValidationError("A verified publication requires activation time.")
        if self.activated_at and self.pk and not self.activation_evidence.filter(status="activation_observed").exists():
            raise ValidationError("Activation time requires independent activation observation.")

    @property
    def approval_2_snapshot_stale(self):
        return self.approval_2_status == "stale"

    @property
    def is_approval_2_stale(self):
        return self.approval_2_snapshot_stale

    @property
    def activation_time(self):
        return self.activated_at


class PublicationCycleSelection(models.Model):
    """Exact media version reviewed for a cycle in one publication revision."""

    publication_batch = models.ForeignKey(SchedulePublicationBatch, on_delete=models.CASCADE, related_name="cycle_selections")
    assignment = models.ForeignKey(WeeklyEpisodeAssignment, on_delete=models.PROTECT, related_name="publication_cycle_selections")
    asset = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="publication_cycle_selections")
    preparation_item = models.ForeignKey(PreparationBatchItem, null=True, blank=True, on_delete=models.PROTECT, related_name="publication_cycle_selections")
    reviewed_snapshot = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["publication_batch", "assignment"], name="unique_publication_cycle_selection")]


class OccurrenceRevisionSelection(models.Model):
    OPERATIONS = [("add", "Add"), ("move", "Move"), ("replace", "Replace"), ("delete", "Delete")]
    publication_batch = models.ForeignKey(SchedulePublicationBatch, on_delete=models.CASCADE, related_name="occurrence_selections")
    occurrence = models.ForeignKey(Occurrence, on_delete=models.PROTECT, related_name="publication_selections")
    occurrence_revision = models.PositiveIntegerField()
    operation = models.CharField(max_length=12, choices=OPERATIONS, default="add")
    source_bin_slot = models.PositiveIntegerField(null=True, blank=True)
    source_bin_record_hash = models.CharField(max_length=64, blank=True)
    selected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["publication_batch", "occurrence"], name="unique_publication_occurrence_selection")]

    def clean(self):
        super().clean()
        if self.occurrence_id and self.occurrence_revision != self.occurrence.revision:
            raise ValidationError("Selected occurrence revision is stale.")
        if self.operation != "add" and (self.source_bin_slot is None or not self.source_bin_record_hash):
            raise ValidationError("Changing an existing controller event requires its exact BIN slot and record SHA-256.")
        if self.operation == "add" and (self.source_bin_slot is not None or self.source_bin_record_hash):
            raise ValidationError("An added event must not claim an existing controller record.")
        if self.source_bin_slot is not None and self.source_bin_slot >= 3000:
            raise ValidationError("BIN schedule slot is outside the qualified table.")
        if self.source_bin_record_hash and (len(self.source_bin_record_hash) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in self.source_bin_record_hash)):
            raise ValidationError("Source BIN record hash must be SHA-256.")


class ArtifactRevision(models.Model):
    ARTIFACT_TYPES = [("nmg", "NMG"), ("bin", "BIN")]
    publication_batch = models.ForeignKey(SchedulePublicationBatch, on_delete=models.PROTECT, related_name="artifact_revisions")
    artifact_type = models.CharField(max_length=8, choices=ARTIFACT_TYPES)
    revision = models.PositiveIntegerField(default=1)
    file_reference = models.CharField(max_length=300)
    content_hash = models.CharField(max_length=128)
    manifest = models.JSONField(default=dict, blank=True)
    validation = models.JSONField(default=dict, blank=True)
    scope = models.CharField(max_length=24, default="change_set")
    base_controller_bin_hash = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["publication_batch", "artifact_type", "revision"], name="unique_artifact_revision")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Artifact revisions are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Artifact revisions are immutable.")


class ActivationEvidence(models.Model):
    publication_batch = models.ForeignKey(SchedulePublicationBatch, on_delete=models.PROTECT, related_name="activation_evidence")
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="activation_evidence")
    recorded_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=32, choices=[(x, x.replace("_", " ").title()) for x in (
        "schedule_transferred", "activation_requested", "activation_acknowledged", "activation_observed",
        "winlgx_reconstructed", "airing_observed", "rolled_back", "ambiguous", "failed", "confirmed", "observed")])
    evidence_hash = models.CharField(max_length=128, blank=True)
    external_reference = models.CharField(max_length=240, blank=True)
    details = models.JSONField(default=dict, blank=True)
    actor = models.CharField(max_length=120, default="owner")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Activation evidence is immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Activation evidence is immutable.")


class ScheduleDeliveryOperation(models.Model):
    """Durable attended delivery journal; recovery never replays LOADSCH."""
    STATES = [(x, x.replace("_", " ").title()) for x in (
        "prepared", "staged", "promotion_started", "schedule_transferred",
        "activation_requested", "activation_acknowledged", "activation_observed",
        "ambiguous", "rollback_started", "rollback_ambiguous", "failed", "rolled_back", "cancelled")]
    ACTIVE = ("prepared", "staged", "promotion_started", "schedule_transferred",
              "activation_requested", "activation_acknowledged", "ambiguous",
              "rollback_started", "rollback_ambiguous")
    publication_batch = models.ForeignKey(SchedulePublicationBatch, on_delete=models.PROTECT, related_name="delivery_operations")
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="schedule_delivery_operations")
    artifact = models.ForeignKey(ArtifactRevision, on_delete=models.PROTECT, related_name="delivery_operations")
    state = models.CharField(max_length=32, choices=STATES, default="prepared")
    approval_hash = models.CharField(max_length=64)
    base_hash = models.CharField(max_length=64)
    rollback_path = models.CharField(max_length=500, blank=True)
    rollback_hash = models.CharField(max_length=64, blank=True)
    staging_path = models.CharField(max_length=500, blank=True)
    remote_backup_path = models.CharField(max_length=500, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    actor = models.CharField(max_length=120, default="owner")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target"], condition=Q(state__in=(
            "prepared", "staged", "promotion_started", "schedule_transferred",
            "activation_requested", "activation_acknowledged", "ambiguous",
            "rollback_started", "rollback_ambiguous"
        )), name="unique_active_schedule_delivery_target")]


class ResearchGate(models.Model):
    STATUS = [(x, x.replace("_", " ").title()) for x in ("open", "passed", "blocked")]
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="research_gates")
    key = models.CharField(max_length=80)
    status = models.CharField(max_length=16, choices=STATUS, default="open")
    evidence = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target", "key"], name="unique_research_gate_per_target")]


class PublicationJob(models.Model):
    KINDS = [(x, x.replace("_", " ").title()) for x in ("preview", "prepare_publication", "generate_nmg", "generate_bin", "guided_delivery", "deliver", "verify", "reconcile")]
    STATUS = [(x, x.replace("_", " ").title()) for x in ("queued", "running", "succeeded", "failed", "cancelled")]
    publication_batch = models.ForeignKey(SchedulePublicationBatch, on_delete=models.CASCADE, related_name="jobs")
    kind = models.CharField(max_length=24, choices=KINDS)
    status = models.CharField(max_length=16, choices=STATUS, default="queued")
    idempotency_key = models.CharField(max_length=160, unique=True)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)


class GuidedEpisodeDelivery(models.Model):
    """Durable owner-facing state for the already-encoded delivery path.

    This is intentionally a thin orchestration record. Media qualification,
    schedule review, staging, activation, and observation remain owned by the
    existing immutable records linked below.
    """
    STATES = [(value, value.replace("_", " ").title()) for value in (
        "intake_review", "intake_confirmed", "media_queued", "media_ready",
        "cycle_planned", "controller_pulled", "change_review", "approval_2",
        "staged", "delivered", "verification_pending", "verified",
        "blocked", "rolled_back", "cancelled")]
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    target = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="guided_episode_deliveries")
    show = models.ForeignKey(Show, on_delete=models.PROTECT, related_name="guided_episode_deliveries")
    episode = models.ForeignKey(Episode, on_delete=models.PROTECT, related_name="guided_deliveries")
    asset = models.ForeignKey(MediaAsset, on_delete=models.PROTECT, related_name="guided_deliveries")
    preparation_batch = models.OneToOneField(PreparationBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="guided_delivery")
    publication_batch = models.OneToOneField(SchedulePublicationBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="guided_delivery")
    cycle_assignment = models.ForeignKey(WeeklyEpisodeAssignment, null=True, blank=True, on_delete=models.PROTECT, related_name="guided_deliveries")
    state = models.CharField(max_length=24, choices=STATES, default="intake_review")
    intake_snapshot = models.JSONField(default=dict, blank=True)
    intake_hash = models.CharField(max_length=64, blank=True)
    change_review = models.JSONField(default=dict, blank=True)
    change_review_hash = models.CharField(max_length=64, blank=True)
    candidate_hash = models.CharField(max_length=128, blank=True)
    current_hash = models.CharField(max_length=128, blank=True)
    rollback_hash = models.CharField(max_length=128, blank=True)
    activation_count = models.PositiveSmallIntegerField(default=0)
    verification_prompted = models.BooleanField(default=False)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.CharField(max_length=120, default="owner")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target", "episode"], condition=Q(state__in=(
            "intake_review", "intake_confirmed", "media_queued", "media_ready",
            "cycle_planned", "controller_pulled", "change_review", "approval_2",
            "staged", "delivered", "verification_pending")), name="unique_active_guided_episode_delivery")]
