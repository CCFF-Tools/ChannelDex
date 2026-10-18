from pathlib import Path
import unittest
from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import Client, TestCase
from django.urls import reverse
from pubtv.operations.forms import OccurrenceForm
from pubtv.operations.models import Episode, MediaAsset, Occurrence, RecurrenceSlot, Show, Station, WeeklyEpisodeAssignment


ROOT = Path(__file__).resolve().parents[1]


class UXOverhaulTests(TestCase):
  def test_show_workspace_separates_discoverable_past_plans_from_upcoming(self):
    from django.utils import timezone
    station = Station.objects.create(name="PUB-TV")
    show = Show.objects.create(station=station, title="Civic", code="civic")
    past = Occurrence.objects.create(station=station, show=show, item_type="live", label="Past council",
      starts_at=timezone.now()-timedelta(days=1), planned_duration_seconds=60)
    future = Occurrence.objects.create(station=station, show=show, item_type="live", label="Future council",
      starts_at=timezone.now()+timedelta(days=1), planned_duration_seconds=60)
    rescheduled = Occurrence.objects.create(station=station, show=show, item_type="live", label="Superseded council",
      starts_at=timezone.now()+timedelta(days=2), planned_duration_seconds=60, status="rescheduled")
    response = Client().get(f"/shows/{show.pk}/")
    assert list(response.context["past_occurrences"]) == [past]
    assert list(response.context["upcoming_occurrences"]) == [future]
    assert rescheduled not in response.context["upcoming_occurrences"]
    assert b'Past planned occurrences' in response.content
    assert '<title>Civic · Shows · ChannelDex</title>' in response.content.decode()

  def test_agenda_uses_canonical_occurrence_label_without_repeating_show_episode(self):
    from django.utils import timezone
    station = Station.objects.create(name="PUB-TV")
    show = Show.objects.create(station=station, title="Civic", code="civic")
    episode = Episode.objects.create(show=show, title="Meeting")
    Occurrence.objects.create(station=station, show=show, episode=episode, item_type="episode",
      label="Civic · Meeting", starts_at=timezone.now()+timedelta(hours=1), planned_duration_seconds=60)
    content = Client().get("/agenda/").content.decode()
    assert "Civic · Meeting · Civic · Meeting" not in content
    assert "Civic · Meeting" in content

  def test_settings_explicitly_groups_workspace_and_application_scope(self):
    Station.objects.create(name="PUB-TV")
    content = Client().get("/settings/").content.decode()
    assert "Workspace-specific settings" in content
    assert "Application-wide settings" in content

  def test_workspace_scoped_show_and_slot_forms_inherit_station(self):
    station = Station.objects.create(name="PUB-TV")
    client = Client()
    show_form = client.get("/shows/new/")
    assert "station" not in show_form.context["form"].fields
    assert show_form.content.decode().count('name="station"') == 1  # shared workspace selector only
    response = client.post("/shows/new/", {"station": 99999, "title": "Inherited", "code": "inherited"})
    assert response.status_code == 200
    assert not Show.objects.filter(code="inherited").exists()
    response = client.post("/shows/new/", {"title": "Inherited", "code": "inherited"})
    assert response.status_code == 302, response.context["form"].errors if response.context else response.content
    show = Show.objects.get(code="inherited")
    assert show.station == station
    slot_form = client.get(f"/slots/new/?show={show.pk}")
    assert "station" not in slot_form.context["form"].fields
    response = client.post(f"/slots/new/?show={show.pk}", {
      "station": 99999, "show": show.pk, "weekday": 0, "start_time": "07:00",
      "duration_seconds_0": "0", "duration_seconds_1": "30", "duration_seconds_2": "0",
      "active_from": "", "active_until": "",
    })
    assert response.status_code == 200
    assert not RecurrenceSlot.objects.filter(show=show).exists()
    response = client.post(f"/slots/new/?show={show.pk}", {
      "show": show.pk, "weekday": 0, "start_time": "07:00",
      "duration_seconds_0": "0", "duration_seconds_1": "30", "duration_seconds_2": "0",
      "active_from": "", "active_until": "",
    })
    assert response.status_code == 302, response.context["form"].errors if response.context else response.content
    assert RecurrenceSlot.objects.get(show=show).station == station

  def test_workspace_switch_returns_object_pages_to_neutral_schedule_context(self):
    first = Station.objects.create(name="PUB-TV")
    second = Station.objects.create(name="GOV-TV")
    client = Client()
    session = client.session
    session["active_station_id"] = first.pk
    session.save()
    response = client.post("/stations/switch/", {"station": second.pk, "next": "/shows/44/?date=2026-10-09&mode=week&show=44"})
    assert response.status_code == 302
    assert response["Location"] == "/schedule/?date=2026-10-09&mode=week"

  def test_workspace_switch_maps_object_collections_without_discarding_media_context(self):
    station = Station.objects.create(name="PUB-TV")
    client = Client()
    response = client.post("/stations/switch/", {"station": station.pk, "next": "/shows/44/?q=ignored"})
    assert response["Location"] == "/library/?q=ignored"
    response = client.post("/stations/switch/", {"station": station.pk, "next": "/media/?show=44&readiness=pending"})
    assert response["Location"] == "/intake/"

  def test_occurrence_workbench_uses_station_local_calendar_date(self):
    from django.utils import timezone
    station = Station.objects.create(name="PUB-TV", timezone="America/Detroit")
    show = Show.objects.create(station=station, title="Late show", code="late-show")
    occurrence = Occurrence.objects.create(
      station=station, show=show, item_type="episode", label="Late",
      starts_at=timezone.make_aware(datetime(2026, 10, 10, 2, 0), dt_timezone.utc),
      planned_duration_seconds=1800,
    )
    client = Client()
    session = client.session; session["active_station_id"] = station.pk; session.save()
    response = client.get(f"/occurrences/{occurrence.pk}/")
    assert response.status_code == 200
    assert response.context["selected_date"].isoformat() == "2026-10-09"
  def test_primary_navigation_is_owner_facing_and_contextual(self):
    base = (ROOT / "pubtv/templates/base.html").read_text(encoding="utf-8")
    for label in ("Work", "Schedule", "Library", "History", "Settings"):
        assert f">{label}</a>" in base
    assert "Dashboard" not in base
    assert "Catalog" not in base
    assert "Operations" not in base

  def test_main_list_and_calendar_views_have_specific_browser_titles(self):
    Station.objects.create(name="PUB-TV")
    expectations = {
      "/library/": "<title>Library · ChannelDex</title>",
      "/day/?date=2026-10-09": "<title>Day plan · ChannelDex</title>",
      "/week/?date=2026-10-09": "<title>Week plan · ChannelDex</title>",
    }
    for path, title in expectations.items():
      response = Client().get(path)
      assert response.status_code == 200
      assert title in response.content.decode()

  def test_schedule_view_switcher_is_styled_and_marks_the_active_view(self):
    css = (ROOT / "pubtv/static/pubtv.css").read_text(encoding="utf-8")
    settings = (ROOT / "pubtv/config/settings.py").read_text(encoding="utf-8")
    assert ".schedule-modes{" in css
    assert ".schedule-modes a[aria-current=page]" in css
    assert '"staticfiles": {"BACKEND": "pubtv.config.static.DevelopmentManifestStaticFilesStorage"}' in settings
    assert "STATICFILES_STORAGE" not in settings
    for mode, label in (("day", "Day"), ("week", "Week"), ("agenda", "Agenda"), ("recurring", "Recurring times")):
      response = Client().get(f"/schedule/?mode={mode}")
      assert response.status_code == 200
      assert f'aria-current="page">{label}</a>' in response.content.decode()

  def test_week_capacity_show_names_wrap_at_word_boundaries(self):
    css = (ROOT / "pubtv/static/pubtv.css").read_text(encoding="utf-8")
    assert ".capacity-show" in css
    assert "overflow-wrap:normal;word-break:normal" in css

  def test_legacy_schedule_urls_mark_the_matching_view(self):
    for path, label in (("/day/", "Day"), ("/week/", "Week"), ("/agenda/", "Agenda"), ("/schedule/recurring/", "Recurring times")):
      response = Client().get(path)
      assert response.status_code == 200
      assert f'aria-current="page">{label}</a>' in response.content.decode()


  def test_today_and_workbench_templates_keep_work_together(self):
    today = (ROOT / "pubtv/templates/work.html").read_text(encoding="utf-8")
    workbench = (ROOT / "pubtv/templates/occurrence_workbench.html").read_text(encoding="utf-8")
    assert "Incoming and unfinished" in today
    assert "Open schedule" in today
    for label in ("Plan", "States", "Preparation", "Publication history", "Air evidence"):
        assert label in workbench

  def test_today_distinguishes_reserved_program_time_from_planned_content(self):
    from django.utils import timezone
    selected = timezone.localdate()
    station = Station.objects.create(name="PUB-TV")
    show = Show.objects.create(station=station, title="Reserved show", code="reserved-show")
    RecurrenceSlot.objects.create(
      station=station, show=show, weekday=selected.weekday(), start_time="07:00",
      duration_seconds=1800, is_premiere=True,
    )
    response = Client().get(f"/?date={selected.isoformat()}")
    assert response.status_code == 200
    assert "Choose episode" in response.content.decode()
    assert "Reserved show" in response.content.decode()
    assert "Reserved show" in response.content.decode()


  def test_legacy_routes_and_new_contextual_routes_are_declared(self):
    urls = (ROOT / "pubtv/config/urls.py").read_text(encoding="utf-8")
    for route in ('path("schedule/"', 'path("day/"', 'path("week/"', 'path("agenda/"', 'path("occurrences/<int:pk>/"'):
        assert route in urls

  def test_media_library_is_primary_and_compatibility_routes_are_not_shadowed(self):
    urls = (ROOT / "pubtv/config/urls.py").read_text(encoding="utf-8")
    base = (ROOT / "pubtv/templates/base.html").read_text(encoding="utf-8")
    assert 'href="{% url \'library\' %}"' in base
    assert ">Library</a>" in base
    assert urls.count('path("schedule/prepare/"') == 1
    assert urls.count('path("media/status/"') == 1
    assert reverse("library") == "/library/"
    assert reverse("media-queue") == "/media/preparation/"

  def test_stale_workspace_poll_blocks_saving_without_automatic_reload(self):
    script = (ROOT / "pubtv/static/media_queue_summary.js").read_text(encoding="utf-8")
    base = (ROOT / "pubtv/templates/base.html").read_text(encoding="utf-8")
    assert "window.location.reload" not in script
    assert "blockStaleWorkspace" in script
    assert "stale-workspace-notice" in base
    assert "Reload for the current channel" in base

  def test_inline_file_pickers_send_rendered_workspace_origin(self):
    device = (ROOT / "pubtv/templates/device_settings.html").read_text(encoding="utf-8")
    guided = (ROOT / "pubtv/templates/guided_delivery.html").read_text(encoding="utf-8")
    assert "body.append('originating_station', origin)" in device
    assert "originating_station: origin" in guided

  def test_agenda_preserves_selected_start_date_across_view_controls(self):
    Station.objects.create(name="PUB-TV")
    response = Client().get("/schedule/?mode=agenda&date=2026-10-09&days=5")
    assert response.status_code == 200
    assert response.context["selected_date"].isoformat() == "2026-10-09"
    assert 'mode=day&amp;date=2026-10-09' in response.content.decode()


  def test_history_is_evidence_focused(self):
    history = (ROOT / "pubtv/templates/history.html").read_text(encoding="utf-8")
    assert "Evidence and audit" in history
    assert "History is read-only" in history or "Evidence" in history
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
    assert "Legacy preparation facts" in workbench
    assert "Quit ChannelDex" in base

  def test_upload_recording_is_contextual_and_day_views_stay_schedule_focused(self):
    today = (ROOT / "pubtv/templates/dashboard.html").read_text(encoding="utf-8")
    day = (ROOT / "pubtv/templates/calendar_day.html").read_text(encoding="utf-8")
    workbench = (ROOT / "pubtv/templates/occurrence_workbench.html").read_text(encoding="utf-8")
    views = (ROOT / "pubtv/operations/views.py").read_text(encoding="utf-8")
    assert 'action="{% url \'upload-create\' %}"' not in today
    assert 'name="occurrence"' not in today
    assert 'name="occurrences"' not in day
    assert "Record schedule upload" in workbench
    assert "Preview and commit uploaded schedule" not in views
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
    slot = RecurrenceSlot.objects.create(station=station, show=show, weekday=0, start_time="07:00", duration_seconds=1800, is_premiere=True)
    assignment = WeeklyEpisodeAssignment.objects.create(show=show, week_start=datetime(2026, 11, 2).date(), premiere_date=datetime(2026, 11, 2).date(), episode=old_episode, selection_type="premiere")
    occurrence = Occurrence.objects.create(station=station, show=show, episode=old_episode, asset=asset, item_type="episode", label=show.title, starts_at=datetime(2026, 11, 2, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=1800, weekly_assignment=assignment, recurrence_slot=slot, schedule_role="premiere")
    data = {"show": show.pk, "premiere_date": "2026-11-02", "episode": new_episode.pk}
    preview = Client().post("/assignments/new/", data)
    assert preview.status_code == 200
    assert "cycle_review_token" in preview.context
    occurrence.refresh_from_db()
    assert occurrence.episode_id == old_episode.pk
    assert occurrence.asset_id == asset.pk
    data.update({"review_token": preview.context["cycle_review_token"], "confirm_review": "1"})
    response = Client().post("/assignments/new/", data)
    assert response.status_code == 302
    occurrence.refresh_from_db()
    assert occurrence.episode_id == new_episode.pk
    assert occurrence.asset_id is None
    assert occurrence.revision == 2
