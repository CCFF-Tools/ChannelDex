from django import forms
from .models import Episode, Occurrence, Show, Station, Device, MediaAsset, Delivery, RecurrenceSlot, WeeklyEpisodeAssignment, Preparation, OccurrenceProgramming, UploadedScheduleRevision, AiringEvidence

class OccurrenceForm(forms.ModelForm):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput, required=False)
    class Meta:
        model = Occurrence
        fields = ["item_type", "label", "show", "episode", "starts_at", "planned_duration_seconds", "status", "reason"]
        widgets = {"starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}
    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station:
            self.fields["show"].queryset = Show.objects.filter(station=station)
            self.fields["episode"].queryset = Episode.objects.filter(show__station=station)

class StationForm(forms.ModelForm):
    class Meta: model = Station; fields = ["name", "timezone"]
    def clean_name(self):
        name = self.cleaned_data["name"]
        if name != "PUB-TV":
            raise forms.ValidationError("V1 is limited to the PUB-TV station boundary.")
        return name
class DeviceForm(forms.ModelForm):
    class Meta: model = Device; fields = ["name"]
class ShowForm(forms.ModelForm):
    class Meta: model = Show; fields = ["station", "title", "code", "producer_name", "producer_contact"]
    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station: self.fields["station"].queryset = Station.objects.filter(pk=station.pk)
class EpisodeForm(forms.ModelForm):
    class Meta: model = Episode; fields = ["show", "title", "producer_id", "intended_air_order", "intended_premiere_date", "runtime_seconds", "status", "received_at"]
    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station: self.fields["show"].queryset = Show.objects.filter(station=station)
class AssetForm(forms.ModelForm):
    class Meta: model = MediaAsset; fields = ["episode", "label", "kind", "version", "runtime_seconds", "smb_reference"]
    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station: self.fields["episode"].queryset = Episode.objects.filter(show__station=station)
class DeliveryForm(forms.ModelForm):
    class Meta: model = Delivery; fields = ["method", "reference", "notified_at", "received_at", "notes", "episodes"]
    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station: self.fields["episodes"].queryset = Episode.objects.filter(show__station=station)
class SlotForm(forms.ModelForm):
    class Meta: model = RecurrenceSlot; fields = ["station", "show", "weekday", "start_time", "duration_seconds", "active_from", "active_until"]
    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station:
            self.fields["station"].queryset = Station.objects.filter(pk=station.pk)
            self.fields["show"].queryset = Show.objects.filter(station=station)
class AssignmentForm(forms.ModelForm):
    class Meta: model = WeeklyEpisodeAssignment; fields = ["show", "week_start", "episode", "selection_type"]
    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        if station:
            self.fields["show"].queryset = Show.objects.filter(station=station)
            self.fields["episode"].queryset = Episode.objects.filter(show__station=station)
class PreparationForm(forms.ModelForm):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput, required=False)
    class Meta: model = Preparation; fields = ["source_available", "ame_preset", "ftp_output_device", "library_registration", "slot_assignment", "uploaded_schedule_revision", "provenance"]
class ProgrammingForm(forms.ModelForm):
    class Meta: model = OccurrenceProgramming; fields = ["device", "note"]
class UploadForm(forms.ModelForm):
    occurrences = forms.ModelMultipleChoiceField(queryset=Occurrence.objects.none(), required=False)
    class Meta: model = UploadedScheduleRevision; fields = ["device", "external_reference", "state", "supersession_reason"]
    def __init__(self, *args, station=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Occurrence.objects.filter(station=station).order_by("starts_at") if station else Occurrence.objects.none()
        self.fields["occurrences"].queryset = qs
class AiringForm(forms.ModelForm):
    class Meta: model = AiringEvidence; fields = ["status", "source", "aired_at", "notes", "supersedes"]
    def __init__(self, *args, occurrence=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supersedes"].queryset = AiringEvidence.objects.filter(occurrence=occurrence) if occurrence else AiringEvidence.objects.none()
