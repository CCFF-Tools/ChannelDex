from pathlib import Path
import unittest
from datetime import datetime, timezone as dt_timezone

from django.test import Client, TestCase
from pubtv.operations.forms import OccurrenceForm
from pubtv.operations.models import Episode, MediaAsset, Occurrence, RecurrenceSlot, Show, Station, WeeklyEpisodeAssignment


ROOT = Path(__file__).resolve().parents[1]


class UXOverhaulTests(unittest.TestCase):
  def test_primary_navigation_is_owner_facing_and_contextual(self):
    base = (ROOT / "pubtv/templates/base.html").read_text(encoding="utf-8")
    for label in ("Today", "Schedule", "Shows", "History", "Settings"):
        assert f">{label}</a>" in base
    assert "Dashboard" not in base
    assert "Catalog" not in base
    assert "Operations" not in base


  def test_today_and_workbench_templates_keep_work_together(self):
    today = (ROOT / "pubtv/templates/dashboard.html").read_text(encoding="utf-8")
    workbench = (ROOT / "pubtv/templates/occurrence_workbench.html").read_text(encoding="utf-8")
    assert "Day timeline" in today
    assert "Open workbench" in today
    for label in ("Plan", "States", "Asset preparation", "Programming", "Schedule upload coverage", "Air evidence"):
        assert label in workbench


  def test_legacy_routes_and_new_contextual_routes_are_declared(self):
    urls = (ROOT / "pubtv/config/urls.py").read_text(encoding="utf-8")
    for route in ('path("schedule/"', 'path("day/"', 'path("week/"', 'path("agenda/"', 'path("occurrences/<int:pk>/"'):
        assert route in urls


  def test_history_is_evidence_focused(self):
    history = (ROOT / "pubtv/templates/history.html").read_text(encoding="utf-8")
    assert "Evidence and audit" in history
    assert "Current actions are available from the occurrence workbench" in history
    assert "Edit plan" not in history

  def test_canonical_forms_do_not_expose_legacy_owner_fields(self):
    forms = (ROOT / "pubtv/operations/forms.py").read_text(encoding="utf-8")
    assert "class AssetPreparationForm" in forms
    assert "class AssetTargetTransferForm" in forms
    assert "legacy_producer_name" not in forms.split("class ShowForm", 1)[1].split("class ShowSlotDurationForm", 1)[0]
    assert "legacy_uploaded_schedule_revision" not in forms.split("class AssetPreparationForm", 1)[1]

  def test_schedule_and_workbench_keep_revision_distinctions(self):
    views = (ROOT / "pubtv/operations/views.py").read_text(encoding="utf-8")
    workbench = (ROOT / "pubtv/templates/occurrence_workbench.html").read_text(encoding="utf-8")
    assert "occurrence_revision == occurrence.revision" in views
    assert "stale_uploads" in workbench
    assert "Chronological day timeline" in (ROOT / "pubtv/templates/calendar_day.html").read_text(encoding="utf-8")

  def test_independent_canonical_workflow_and_compatibility_aliases(self):
    views = (ROOT / "pubtv/operations/views.py").read_text(encoding="utf-8")
    urls = (ROOT / "pubtv/config/urls.py").read_text(encoding="utf-8")
    template = (ROOT / "pubtv/templates/asset_preparation_form.html").read_text(encoding="utf-8")
    assert '"save_preparation" in request.POST' in views
    assert "transfer_create" in views and "transfer_edit" in views
    assert "update_or_create" in views
    assert 'name="reserved-schedule"' in urls
    assert 'name="save_preparation"' in template

  def test_workbench_shows_every_transfer_and_migration_history(self):
    workbench = (ROOT / "pubtv/templates/occurrence_workbench.html").read_text(encoding="utf-8")
    base = (ROOT / "pubtv/templates/base.html").read_text(encoding="utf-8")
    assert "for transfer in transfers" in workbench
    assert "Legacy preparation facts (read-only migration history)" in workbench
    assert "Quit ChannelDex" in base

  def test_today_and_day_offer_repeated_occurrence_upload_selection(self):
    today = (ROOT / "pubtv/templates/dashboard.html").read_text(encoding="utf-8")
    day = (ROOT / "pubtv/templates/calendar_day.html").read_text(encoding="utf-8")
    views = (ROOT / "pubtv/operations/views.py").read_text(encoding="utf-8")
    assert 'action="{% url \'upload-create\' %}"' in today
    assert 'name="occurrence"' in today
    assert 'name="occurrences"' in day
    assert "request.GET.getlist(\"occurrence\")" in views
    assert "request.GET.getlist(\"occurrences\")" in views
    assert "station=station, status=\"planned\"" in views


class OccurrenceAssetEditTests(TestCase):
  def test_occurrence_edit_persists_asset_and_increments_revision(self):
    station = Station.objects.create()
    show = Show.objects.create(station=station, title="Show", code="show")
    episode = Episode.objects.create(show=show, title="Episode")
    asset = MediaAsset.objects.create(episode=episode, file_name="episode.mxf", kind="source", version="v1")
    occurrence = Occurrence.objects.create(station=station, show=show, episode=episode, item_type="episode", label="Episode", starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    response = Client().post(f"/occurrences/{occurrence.pk}/edit/", {
      "expected_revision": "1", "item_type": "episode", "label": "", "show": show.pk,
      "episode": episode.pk, "asset": asset.pk, "starts_at": "2026-01-05T12:00",
      "planned_duration_seconds_0": "0", "planned_duration_seconds_1": "1", "planned_duration_seconds_2": "0",
      "status": "planned", "reason": "",
    })
    assert response.status_code == 302
    occurrence.refresh_from_db()
    assert occurrence.asset_id == asset.pk
    assert occurrence.revision == 2

  def test_occurrence_form_rejects_asset_from_another_episode(self):
    station = Station.objects.create()
    show = Show.objects.create(station=station, title="Show", code="show")
    episode = Episode.objects.create(show=show, title="Episode")
    other = Episode.objects.create(show=show, title="Other")
    asset = MediaAsset.objects.create(episode=other, file_name="other.mxf", kind="source", version="v1")
    form = OccurrenceForm(data={"item_type": "episode", "label": "", "show": show.pk, "episode": episode.pk, "asset": asset.pk, "starts_at": "2026-01-05T12:00", "planned_duration_seconds_0": "0", "planned_duration_seconds_1": "1", "planned_duration_seconds_2": "0", "status": "planned", "reason": ""}, station=station)
    assert not form.is_valid()
    assert "asset" in form.errors

  def test_premiere_correction_clears_old_episode_asset(self):
    station = Station.objects.create()
    show = Show.objects.create(station=station, title="Show", code="show")
    old_episode = Episode.objects.create(show=show, title="Old")
    new_episode = Episode.objects.create(show=show, title="New")
    asset = MediaAsset.objects.create(episode=old_episode, file_name="old.mxf", kind="source", version="v1")
    RecurrenceSlot.objects.create(station=station, show=show, weekday=0, start_time="07:00", duration_seconds=1800, is_premiere=True)
    assignment = WeeklyEpisodeAssignment.objects.create(show=show, week_start=datetime(2026, 1, 5).date(), premiere_date=datetime(2026, 1, 5).date(), episode=old_episode, selection_type="premiere")
    occurrence = Occurrence.objects.create(station=station, show=show, episode=old_episode, asset=asset, item_type="episode", label=show.title, starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=1800, weekly_assignment=assignment)
    response = Client().post("/assignments/new/", {"show": show.pk, "premiere_date": "2026-01-05", "episode": new_episode.pk})
    assert response.status_code == 302
    occurrence.refresh_from_db()
    assert occurrence.episode_id == new_episode.pk
    assert occurrence.asset_id is None
    assert occurrence.revision == 2
