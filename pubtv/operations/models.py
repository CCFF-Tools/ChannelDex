from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

class Station(models.Model):
    name = models.CharField(max_length=120, default="PUB-TV")
    timezone = models.CharField(max_length=64, default="America/Detroit")

class Show(models.Model):
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="shows")
    title = models.CharField(max_length=160)
    code = models.SlugField(max_length=40)
    producer_name = models.CharField(max_length=160, blank=True)
    producer_contact = models.CharField(max_length=240, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["station", "code"], name="unique_station_show_code")]
    def __str__(self): return self.title

class Episode(models.Model):
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="episodes")
    title = models.CharField(max_length=160)
    producer_id = models.CharField(max_length=120, blank=True)
    intended_air_order = models.PositiveIntegerField(null=True, blank=True)
    intended_premiere_date = models.DateField(null=True, blank=True)
    runtime_seconds = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=[("pending", "Pending"), ("previously_scheduled", "Previously scheduled")], default="pending")
    received_at = models.DateTimeField(null=True, blank=True)
    def __str__(self): return f"{self.show.code}: {self.title}"

class MediaAsset(models.Model):
    episode = models.ForeignKey(Episode, null=True, blank=True, on_delete=models.SET_NULL, related_name="assets")
    label = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=[("source", "Source"), ("encoded", "Encoded")])
    version = models.CharField(max_length=80, default="v1")
    runtime_seconds = models.PositiveIntegerField(null=True, blank=True)
    smb_reference = models.CharField(max_length=300, blank=True)

class Delivery(models.Model):
    method = models.CharField(max_length=16, choices=[("dropbox", "Dropbox"), ("email", "Email"), ("smb", "SMB")])
    reference = models.CharField(max_length=300)
    notified_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    episodes = models.ManyToManyField(Episode, blank=True, related_name="deliveries")

class Device(models.Model):
    name = models.CharField(max_length=120, unique=True)

class RecurrenceSlot(models.Model):
    WEEKDAYS = [(i, str(i)) for i in range(7)]
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="slots")
    show = models.ForeignKey(Show, null=True, blank=True, on_delete=models.CASCADE, related_name="slots")
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    duration_seconds = models.PositiveIntegerField()
    active_from = models.DateField(null=True, blank=True)
    active_until = models.DateField(null=True, blank=True)
    def clean(self):
        if self.show_id and self.show and self.station_id != self.show.station_id:
            raise ValidationError("Recurrence slot show must belong to its station.")
        if self.active_from and self.active_until and self.active_from > self.active_until:
            raise ValidationError("Recurrence slot active dates are reversed.")

class WeeklyEpisodeAssignment(models.Model):
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name="weekly_assignments")
    week_start = models.DateField()
    episode = models.ForeignKey(Episode, null=True, blank=True, on_delete=models.SET_NULL)
    selection_type = models.CharField(max_length=16, choices=[("premiere", "New premiere"), ("rerun", "Selected older rerun"), ("none", "No program")])
    class Meta:
        constraints = [models.UniqueConstraint(fields=["show", "week_start"], name="unique_show_week")]
    def clean(self):
        if self.week_start and self.week_start.weekday() != 0:
            raise ValidationError("Week start must be a Monday.")
        if self.episode_id and self.episode and self.episode.show_id != self.show_id:
            raise ValidationError("Assigned episode must belong to the assigned show.")
        if self.selection_type == "none" and self.episode_id:
            raise ValidationError("A no-program assignment cannot include an episode.")
        if self.selection_type in {"premiere", "rerun"} and not self.episode_id:
            raise ValidationError("A premiere or rerun assignment requires an episode.")

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

class Preparation(models.Model):
    occurrence = models.OneToOneField(Occurrence, on_delete=models.CASCADE, related_name="preparation")
    source_available = models.CharField(max_length=12, choices=[("yes", "Yes"), ("no", "No"), ("na", "N/A")], default="no")
    ame_preset = models.CharField(max_length=160, blank=True)
    ftp_output_device = models.CharField(max_length=200, blank=True)
    library_registration = models.CharField(max_length=200, blank=True)
    slot_assignment = models.CharField(max_length=200, blank=True)
    uploaded_schedule_revision = models.CharField(max_length=120, blank=True)
    provenance = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)
    source_available_actor = models.CharField(max_length=120, blank=True); source_available_at = models.DateTimeField(null=True, blank=True); source_available_provenance = models.TextField(blank=True)
    ame_actor = models.CharField(max_length=120, blank=True); ame_at = models.DateTimeField(null=True, blank=True); ame_provenance = models.TextField(blank=True)
    ftp_actor = models.CharField(max_length=120, blank=True); ftp_at = models.DateTimeField(null=True, blank=True); ftp_provenance = models.TextField(blank=True)
    library_actor = models.CharField(max_length=120, blank=True); library_at = models.DateTimeField(null=True, blank=True); library_provenance = models.TextField(blank=True)
    slot_actor = models.CharField(max_length=120, blank=True); slot_at = models.DateTimeField(null=True, blank=True); slot_provenance = models.TextField(blank=True)
    upload_actor = models.CharField(max_length=120, blank=True); upload_at = models.DateTimeField(null=True, blank=True); upload_provenance = models.TextField(blank=True)

class UploadedScheduleRevision(models.Model):
    STATE = [(x, x.title()) for x in ("active", "superseded", "invalidated")]
    device = models.ForeignKey(Device, on_delete=models.PROTECT)
    external_reference = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    state = models.CharField(max_length=16, choices=STATE, default="active")
    occurrences = models.ManyToManyField(Occurrence, through="UploadedOccurrenceCoverage", blank=True, related_name="uploaded_revisions")
    supersession_reason = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)

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
    confirmed_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)

class AiringEvidence(models.Model):
    occurrence = models.ForeignKey(Occurrence, null=True, blank=True, on_delete=models.SET_NULL, related_name="airing_evidence")
    status = models.CharField(max_length=20, choices=[("reported", "Reported/observed"), ("log_verified", "Log verified")])
    source = models.CharField(max_length=200)
    aired_at = models.DateTimeField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=120, blank=True)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrections")

class AuditEvent(models.Model):
    actor = models.CharField(max_length=120, default="owner")
    occurred_at = models.DateTimeField(default=timezone.now)
    action = models.CharField(max_length=40)
    entity = models.CharField(max_length=80)
    entity_id = models.PositiveIntegerField(null=True, blank=True)
    revision = models.PositiveIntegerField(null=True, blank=True)
    summary = models.TextField()
