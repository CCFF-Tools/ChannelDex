from datetime import timedelta

from django import forms
from django.utils import timezone

from .models import (
    AiringEvidence, Delivery, Device, Episode, EpisodeWorkflowMilestone,
    MediaAsset, Occurrence, OccurrenceProgramming, Preparation, Producer,
    RecurrenceSlot, Show, Station, UploadedScheduleRevision,
    WeeklyEpisodeAssignment,
)


class DurationWidget(forms.MultiWidget):
    """Owner-facing hours/minutes controls backed by integer seconds."""

    def __init__(self, attrs=None):
        widgets = [
            forms.NumberInput(attrs={"min": 0, "step": 1, "aria-label": "Hours", "placeholder": "Hours"}),
            forms.NumberInput(attrs={"min": 0, "max": 59, "step": 1, "aria-label": "Minutes", "placeholder": "Minutes"}),
            forms.HiddenInput(),
        ]
        super().__init__(widgets, attrs)

    def decompress(self, value):
        if value in (None, ""):
            return [None, None, 0]
        seconds = int(value)
        return [seconds // 3600, (seconds % 3600) // 60, seconds % 60]

    def value_from_datadict(self, data, files, name):
        values = super().value_from_datadict(data, files, name)
        if all(value in (None, "") for value in values) and data.get(name) not in (None, ""):
            return self.decompress(data.get(name))
        return values


class DurationField(forms.MultiValueField):
    widget = DurationWidget

    def __init__(self, *args, **kwargs):
        fields = [
            forms.IntegerField(min_value=0, required=False),
            forms.IntegerField(min_value=0, max_value=59, required=False),
            forms.IntegerField(min_value=0, max_value=59, required=False),
        ]
        kwargs.setdefault("help_text", "Enter hours and minutes. Existing second precision is preserved.")
        super().__init__(fields=fields, require_all_fields=False, *args, **kwargs)

    def compress(self, data_list):
        if not data_list or all(value in (None, "") for value in data_list):
            if self.required:
                raise forms.ValidationError("Enter a duration in hours and minutes.")
            return None
        hours, minutes, seconds = [(value or 0) for value in data_list]
        total = hours * 3600 + minutes * 60 + seconds
        if self.required and total <= 0:
            raise forms.ValidationError("Duration must be greater than zero.")
        return total


class OccurrenceForm(forms.ModelForm):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput, required=False)
    planned_duration_seconds = DurationField(label="Planned length")

    class Meta:
        model = Occurrence
        fields = ["item_type", "label", "show", "episode", "starts_at", "planned_duration_seconds", "status", "reason"]
        labels = {"starts_at": "Start date and time"}
        help_texts = {
            "starts_at": "Interpreted in the PUB-TV station timezone (America/Detroit).",
            "status": "This is planned schedule status, not evidence that the item aired.",
        }
        widgets = {"starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["starts_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        if station:
            self.fields["show"].queryset = Show.objects.filter(station=station)
            self.fields["episode"].queryset = Episode.objects.filter(show__station=station)


class StationForm(forms.ModelForm):
    class Meta:
        model = Station
        fields = ["name", "timezone"]
        help_texts = {"timezone": "Scheduling stays in America/Detroit, independent of the Mac's timezone."}

    def clean_name(self):
        name = self.cleaned_data["name"]
        if name != "PUB-TV":
            raise forms.ValidationError("V1 is limited to the PUB-TV station boundary.")
        return name


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ["name"]
        help_texts = {"name": "The exact playback or schedule-upload device name."}


class ShowForm(forms.ModelForm):
    class Meta:
        model = Show
        fields = ["station", "title", "description", "code", "show_type", "show_type_other", "producer_name", "producer_phone", "producer_email", "website", "youtube", "facebook", "instagram", "primary_delivery_method", "primary_delivery_other"]
        labels = {
            "code": "Show code", "youtube": "YouTube link",
            "facebook": "Facebook link", "instagram": "Instagram link",
            "primary_delivery_other": "Describe other delivery method",
            "show_type_other": "Describe other show type",
        }
        help_texts = {
            "code": "A short stable label used for local organization.",
            "primary_delivery_method": "A preference only; each actual delivery records its own method and provenance.",
            "show_type": "Optional controlled classification used by calendar filters.",
        }

    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station:
            self.fields["station"].queryset = Station.objects.filter(pk=station.pk)

    def clean(self):
        data = super().clean()
        if data.get("primary_delivery_method") == "other" and not data.get("primary_delivery_other", "").strip():
            self.add_error("primary_delivery_other", "Describe the other delivery method.")
        elif data.get("primary_delivery_method") != "other":
            data["primary_delivery_other"] = ""
        if data.get("show_type") == "other" and not data.get("show_type_other", "").strip():
            self.add_error("show_type_other", "Describe other show type.")
        elif data.get("show_type") != "other":
            data["show_type_other"] = ""
        return data


class ShowSlotDurationForm(forms.ModelForm):
    slot_duration_seconds = DurationField(label="Time slot length")

    class Meta:
        model = Show
        fields = ["slot_duration_seconds"]
        help_texts = {"slot_duration_seconds": "This length applies to the premiere and every replay for this show. Existing planned occurrences keep their recorded length."}


class ProducerForm(forms.ModelForm):
    class Meta:
        model = Producer
        fields = ["first_name", "last_name", "phone", "email", "external_membership_number", "notes"]
        labels = {"external_membership_number": "External membership number (optional)"}
        help_texts = {
            "phone": "Private contact metadata; it is not transmitted externally.",
            "email": "Private contact metadata; it is not transmitted externally.",
            "external_membership_number": "Reserved for possible future MyTurn association; no lookup or integration occurs.",
        }


class EpisodeForm(forms.ModelForm):
    runtime_seconds = DurationField(label="Episode runtime", required=False)

    class Meta:
        model = Episode
        fields = ["show", "title", "producer", "intended_air_order", "intended_premiere_date", "runtime_seconds", "received_at"]
        labels = {
            "intended_air_order": "Planned episode order",
            "intended_premiere_date": "Planned premiere date",
            "received_at": "Episode received date and time",
        }
        help_texts = {
            "producer": "Choose the producer by name. The internal record ID is never used as their display name.",
            "intended_air_order": "Lower numbers air first; producer direction may override the usual FIFO order.",
            "received_at": "When PUB-TV received or accepted the episode – not when it aired or was encoded.",
        }
        widgets = {
            "intended_premiere_date": forms.DateInput(attrs={"type": "date"}),
            "received_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["received_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        if station:
            self.fields["show"].queryset = Show.objects.filter(station=station)
            self.fields["producer"].queryset = Producer.objects.filter(station=station)
        initial = kwargs.get("initial") or {}
        if initial.get("show"):
            self.fields["show"].initial = initial["show"]
        if initial.get("producer"):
            self.fields["producer"].initial = initial["producer"]


class EpisodeMilestoneForm(forms.ModelForm):
    STAGE_HELP = {
        "received": "PUB-TV received the submission or delivery notice.",
        "downloaded": "The source file was copied into PUB-TV's working or storage workflow.",
        "encoded": "The required encoded rendition was produced and recorded.",
        "transferred": "The encoded file was transferred to the target playback system.",
        "scheduled": "The episode was placed in a recorded schedule revision; this is not proof of airing.",
    }

    class Meta:
        model = EpisodeWorkflowMilestone
        fields = ["stage", "completed_at", "actor", "provenance", "notes"]
        labels = {"completed_at": "Completed date and time"}
        help_texts = {
            "stage": "Received → Downloaded → Encoded → Transferred → Scheduled. Each completed stage remains in history.",
            "completed_at": "Interpreted in America/Detroit.",
            "provenance": "How this manual fact was confirmed.",
        }
        widgets = {"completed_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["completed_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["stage"].widget.attrs["title"] = " | ".join(
            f"{label}: {self.STAGE_HELP[value]}" for value, label in EpisodeWorkflowMilestone.STAGES
        )


class AssetForm(forms.ModelForm):
    runtime_seconds = DurationField(label="Media runtime", required=False)
    asset_id = forms.CharField(label="Asset ID", required=False, disabled=True)

    class Meta:
        model = MediaAsset
        fields = ["episode", "file_name", "label", "kind", "version", "runtime_seconds", "smb_reference"]
        labels = {"file_name": "File name", "label": "Legacy display label"}
        widgets = {"asset_id": forms.TextInput(attrs={"readonly": True}), "label": forms.HiddenInput()}
        help_texts = {"smb_reference": "A private metadata reference only; PUB-TV does not mount, fetch, move, or rename the file."}

    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station:
            self.fields["episode"].queryset = Episode.objects.filter(show__station=station)
        initial = kwargs.get("initial") or {}
        if initial.get("episode"):
            self.fields["episode"].initial = initial["episode"]
        self.fields["asset_id"].required = False
        if self.instance and self.instance.pk:
            self.fields["asset_id"].initial = str(self.instance.asset_id)

    def clean(self):
        data = super().clean()
        filename = (data.get("file_name") or data.get("label") or "").strip()
        if not filename:
            self.add_error("file_name", "Enter the file name for this asset.")
        data["file_name"] = filename
        data["label"] = filename
        return data


class DeliveryForm(forms.ModelForm):
    class Meta:
        model = Delivery
        fields = ["method", "other_method", "reference", "notified_at", "received_at", "notes", "episodes"]
        labels = {
            "other_method": "Describe other delivery method",
            "notified_at": "Delivery notice date and time",
            "received_at": "File received date and time",
            "episodes": "Linked episodes",
        }
        help_texts = {"reference": "Metadata only. PUB-TV does not fetch links, connect to accounts, or mount storage."}
        widgets = {
            "notified_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "received_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("notified_at", "received_at"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M"]
        if station:
            self.fields["episodes"].queryset = Episode.objects.filter(show__station=station)
        initial = kwargs.get("initial") or {}
        if initial.get("episode"):
            self.fields["episodes"].initial = [initial["episode"]]

    def clean(self):
        data = super().clean()
        if data.get("method") == "other" and not data.get("other_method", "").strip():
            self.add_error("other_method", "Describe the other delivery method.")
        elif data.get("method") != "other":
            data["other_method"] = ""
        return data


class SlotForm(forms.ModelForm):
    duration_seconds = DurationField(label="Time slot length")

    class Meta:
        model = RecurrenceSlot
        fields = ["station", "show", "weekday", "start_time", "duration_seconds", "is_premiere", "active_from", "active_until"]
        labels = {"is_premiere": "Primary / new-episode premiere slot"}
        help_texts = {
            "start_time": "Local station time in America/Detroit. Type or step through hours and minutes.",
            "is_premiere": "Exactly one currently active slot per show should be the premiere slot; other slots replay that week's selected episode.",
            "active_from": "Advanced: optional effective date for future or seasonal changes.",
            "active_until": "Advanced: optional end date; leave blank for an ongoing slot.",
        }
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time", "step": 60}, format="%H:%M"),
            "active_from": forms.DateInput(attrs={"type": "date"}),
            "active_until": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, station=None, show=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_time"].input_formats = ["%H:%M"]
        if station:
            self.fields["station"].queryset = Station.objects.filter(pk=station.pk)
            self.fields["show"].queryset = Show.objects.filter(station=station)
            self.fields["station"].initial = station
        if show:
            self.fields["show"].initial = show
            self.fields["show"].disabled = True
            self.fields["station"].disabled = True
            if show.slot_duration_seconds:
                self.fields["duration_seconds"].initial = show.slot_duration_seconds
                self.fields["duration_seconds"].disabled = True
                self.fields["duration_seconds"].help_text = "Inherited from the show's time slot length. Change it from the weekly-time manager."


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = WeeklyEpisodeAssignment
        fields = ["show", "premiere_date", "episode"]
        labels = {"premiere_date": "Premiere date", "episode": "Episode for this premiere cycle"}
        help_texts = {
            "premiere_date": "Defaults to the next configured premiere. Replays after this premiere use the same episode until the following premiere.",
            "episode": "The next pending episode is suggested automatically. Choose an older episode explicitly, or leave blank only when no episode should run in this cycle.",
        }
        widgets = {"premiere_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["show"].widget.attrs["data-cycle-show"] = "true"
        if station:
            self.fields["show"].queryset = Show.objects.filter(station=station)
            self.fields["episode"].queryset = Episode.objects.filter(show__station=station)
    def clean(self):
        data = super().clean()
        show = data.get("show")
        premiere_date = data.get("premiere_date")
        episode = data.get("episode")
        if show and episode and episode.show_id != show.pk:
            self.add_error("episode", "Choose an episode from this show.")
        if show and premiere_date:
            self.instance.week_start = premiere_date - timedelta(days=premiere_date.weekday())
            self.instance.selection_type = (
                "none" if not episode else ("premiere" if episode.status == "pending" else "rerun")
            )
            active_slots = []
            for slot in show.slots.all():
                offset = (slot.weekday - premiere_date.weekday()) % 7
                slot_date = premiere_date + timedelta(days=offset)
                if (not slot.active_from or slot.active_from <= slot_date) and (
                    not slot.active_until or slot.active_until >= slot_date
                ):
                    active_slots.append(slot)
            if not active_slots:
                self.add_error("show", "This show has no active weekly times for that premiere cycle. Manage its weekly times first.")
            elif sum(slot.is_premiere for slot in active_slots) != 1:
                self.add_error("show", "This show must have exactly one active premiere slot for that date.")
        return data


class PreparationForm(forms.ModelForm):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Preparation
        fields = ["source_available", "ame_preset", "ftp_output_device", "library_registration", "slot_assignment", "uploaded_schedule_revision"]
        labels = {
            "ame_preset": "Adobe Media Encoder preset",
            "ftp_output_device": "FTP destination device",
            "library_registration": "WinLGX library registration",
            "slot_assignment": "WinLGX playback slot",
            "uploaded_schedule_revision": "Uploaded schedule revision",
        }
        help_texts = {
            "library_registration": "Record the name or identifier used when registering the encoded file in WinLGX.",
            "slot_assignment": "Record the WinLGX playback slot assigned to the registered file.",
        }


class ProgrammingForm(forms.ModelForm):
    class Meta:
        model = OccurrenceProgramming
        fields = ["device", "note"]


class UploadForm(forms.ModelForm):
    occurrences = forms.ModelMultipleChoiceField(queryset=Occurrence.objects.none(), required=False)

    class Meta:
        model = UploadedScheduleRevision
        fields = ["device", "external_reference", "state", "supersession_reason"]
        help_texts = {"external_reference": "Optional operator-entered label or reference for this exact upload."}

    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Occurrence.objects.filter(station=station).order_by("starts_at") if station else Occurrence.objects.none()
        self.fields["occurrences"].queryset = qs


class AiringForm(forms.ModelForm):
    class Meta:
        model = AiringEvidence
        fields = ["status", "source", "aired_at", "notes", "supersedes"]
        labels = {"aired_at": "Aired date and time"}
        help_texts = {"source": "Identify the report, observation, or log that supports this evidence."}
        widgets = {"aired_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, occurrence=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["aired_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["supersedes"].queryset = AiringEvidence.objects.filter(occurrence=occurrence) if occurrence else AiringEvidence.objects.none()
