from datetime import datetime, timezone as dt_timezone
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.test import Client
from pubtv.operations.models import Episode, Occurrence, Show, Station, Device, UploadedScheduleRevision, RecurrenceSlot, OccurrenceProgramming, Preparation, AiringEvidence, WeeklyEpisodeAssignment, UploadedOccurrenceCoverage
from pubtv.operations.services import advance_passed_premiere, occurrences_for_slot, schedule_alerts, confirm_preparation_fact, revise_airing

class CoreRulesTests(TestCase):
  def test_episode_queue_is_producer_ordered(self):
    station = Station.objects.create(); show = Show.objects.create(station=station, title="News", code="news")
    later = Episode.objects.create(show=show, title="Later", intended_air_order=2)
    first = Episode.objects.create(show=show, title="First", intended_air_order=1)
    assert list(show.episodes.filter(status="pending").order_by("intended_air_order")) == [first, later]

  def test_occurrence_over_slot_and_live_validation(self):
    station = Station.objects.create(); show = Show.objects.create(station=station, title="Show", code="show")
    ep = Episode.objects.create(show=show, title="Long", runtime_seconds=1900)
    occurrence = Occurrence(station=station, show=show, episode=ep, item_type="episode", label="Long", starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=1800)
    assert occurrence.over_slot
    live = Occurrence(station=station, item_type="live", label="Live", starts_at=occurrence.starts_at, planned_duration_seconds=60, asset=None)
    live.full_clean()

  def test_stale_revision_rejected(self):
    station = Station.objects.create(); item = Occurrence.objects.create(station=station, item_type="filler", label="Filler", starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    client = Client(); response = client.post(f"/occurrences/{item.pk}/edit/", {"expected_revision": 0, "item_type": "filler", "label": "Changed", "starts_at": "2026-01-05T12:00", "planned_duration_seconds": 60, "status": "planned", "reason": ""})
    assert response.status_code == 400

  def test_overlap_is_visible(self):
    station = Station.objects.create()
    first = Occurrence.objects.create(station=station, item_type="filler", label="A", starts_at=datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    second = Occurrence.objects.create(station=station, item_type="filler", label="B", starts_at=datetime(2026, 1, 5, 12, 0, 30, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    assert schedule_alerts([first, second])[0][0] == "overlap"

  def test_recurrence_uses_station_local_wall_clock(self):
    station = Station.objects.create(); show = Show.objects.create(station=station, title="Weekly", code="weekly")
    first = Episode.objects.create(show=show, title="First")
    second = Episode.objects.create(show=show, title="Second")
    WeeklyEpisodeAssignment.objects.create(show=show, week_start=datetime(2026, 1, 5).date(), episode=first, selection_type="premiere")
    WeeklyEpisodeAssignment.objects.create(show=show, week_start=datetime(2026, 1, 12).date(), episode=second, selection_type="premiere")
    slot = RecurrenceSlot.objects.create(station=station, show=show, weekday=0, start_time="07:00", duration_seconds=1800)
    generated = occurrences_for_slot(slot, datetime(2026, 1, 5).date(), datetime(2026, 1, 12).date())
    assert len(generated) == 2 and generated[0].starts_at.hour == 7

  def test_passed_premiere_requires_active_upload_and_is_idempotent(self):
    station = Station.objects.create(); show = Show.objects.create(station=station, title="Show", code="show")
    episode = Episode.objects.create(show=show, title="Premiere")
    occurrence = Occurrence.objects.create(station=station, show=show, episode=episode, item_type="episode", schedule_role="premiere", label="Premiere", starts_at=datetime(2020, 1, 5, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    assignment = WeeklyEpisodeAssignment.objects.create(show=show, week_start=datetime(2019, 12, 30).date(), episode=episode, selection_type="premiere")
    Occurrence.objects.filter(pk=occurrence.pk).update(weekly_assignment=assignment); occurrence.refresh_from_db()
    device = Device.objects.create(name="Ultra-Nexus HD")
    assert not advance_passed_premiere(occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
    upload = UploadedScheduleRevision.objects.create(device=device, state="active")
    upload.occurrences.add(occurrence)
    OccurrenceProgramming.objects.create(occurrence=occurrence, device=device)
    assert advance_passed_premiere(occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
    assert not advance_passed_premiere(occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))

  def _uploaded(self, occurrence, state="active", device=None, revision=None):
    device = device or Device.objects.create(name=f"device-{UploadedScheduleRevision.objects.count()}")
    upload = UploadedScheduleRevision.objects.create(device=device, state=state)
    UploadedOccurrenceCoverage.objects.create(upload=upload, occurrence=occurrence, occurrence_revision=revision or occurrence.revision)
    OccurrenceProgramming.objects.create(occurrence=occurrence, device=device)
    return upload

  def test_stale_coverage_after_occurrence_edit_does_not_advance(self):
    station = Station.objects.create(); show = Show.objects.create(station=station, title="S", code="s"); ep = Episode.objects.create(show=show, title="E")
    occurrence = Occurrence.objects.create(station=station, show=show, episode=ep, item_type="episode", label="E", starts_at=datetime(2020, 1, 1, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    self._uploaded(occurrence); Occurrence.objects.filter(pk=occurrence.pk).update(revision=2); occurrence.refresh_from_db()
    assert not advance_passed_premiere(occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))

  def test_cancelled_preempted_superseded_and_invalidated_uploads_fail(self):
    for status in ("cancelled", "preempted"):
      station = Station.objects.create(); show = Show.objects.create(station=station, title=status, code=status); ep = Episode.objects.create(show=show, title="E")
      occurrence = Occurrence.objects.create(station=station, show=show, episode=ep, item_type="episode", label="E", starts_at=datetime(2020, 1, 1, tzinfo=dt_timezone.utc), planned_duration_seconds=60, status=status)
      self._uploaded(occurrence)
      assert not advance_passed_premiere(occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
    station = Station.objects.create(); show = Show.objects.create(station=station, title="S", code="sup"); ep = Episode.objects.create(show=show, title="E")
    occurrence = Occurrence.objects.create(station=station, show=show, episode=ep, item_type="episode", label="E", starts_at=datetime(2020, 1, 1, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    for state in ("superseded", "invalidated"):
      self._uploaded(occurrence, state=state)
      assert not advance_passed_premiere(occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))

  def test_programming_device_mismatch_fails(self):
    station = Station.objects.create(); show = Show.objects.create(station=station, title="S", code="m"); ep = Episode.objects.create(show=show, title="E")
    occurrence = Occurrence.objects.create(station=station, show=show, episode=ep, item_type="episode", label="E", starts_at=datetime(2020, 1, 1, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    upload_device = Device.objects.create(name="upload"); other = Device.objects.create(name="other")
    upload = UploadedScheduleRevision.objects.create(device=upload_device); UploadedOccurrenceCoverage.objects.create(upload=upload, occurrence=occurrence, occurrence_revision=1); OccurrenceProgramming.objects.create(occurrence=occurrence, device=other)
    assert not advance_passed_premiere(occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))

  def test_rerun_or_missing_or_mismatched_assignment_never_advances_queue(self):
    station = Station.objects.create(); show = Show.objects.create(station=station, title="S", code="rerun"); ep = Episode.objects.create(show=show, title="E")
    occurrence = Occurrence.objects.create(station=station, show=show, episode=ep, item_type="episode", label="E", starts_at=datetime(2020, 1, 1, tzinfo=dt_timezone.utc), planned_duration_seconds=60)
    self._uploaded(occurrence)
    assignment = WeeklyEpisodeAssignment.objects.create(show=show, week_start=datetime(2019, 12, 30).date(), episode=ep, selection_type="rerun")
    for selection, assigned_episode in (("rerun", ep), ("premiere", None)):
      assignment.selection_type = selection; assignment.episode = assigned_episode; assignment.save()
      Occurrence.objects.filter(pk=occurrence.pk).update(weekly_assignment=assignment); occurrence.refresh_from_db()
      assert not advance_passed_premiere(occurrence, now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))

  def test_same_week_assignment_links_replays_and_boundary_requires_new_assignment(self):
    station = Station.objects.create(); show = Show.objects.create(station=station, title="S", code="week"); ep = Episode.objects.create(show=show, title="E")
    WeeklyEpisodeAssignment.objects.create(show=show, week_start=datetime(2026, 1, 5).date(), episode=ep, selection_type="premiere")
    slot = RecurrenceSlot.objects.create(station=station, show=show, weekday=0, start_time="07:00", duration_seconds=60)
    assert occurrences_for_slot(slot, datetime(2026, 1, 5).date(), datetime(2026, 1, 5).date())[0].episode == ep
    assert occurrences_for_slot(slot, datetime(2026, 1, 12).date(), datetime(2026, 1, 12).date()) == []
    WeeklyEpisodeAssignment.objects.create(show=show, week_start=datetime(2026, 1, 12).date(), selection_type="none")
    assert occurrences_for_slot(slot, datetime(2026, 1, 12).date(), datetime(2026, 1, 12).date()) == []

  def test_cross_station_and_cross_show_integrity_and_tampered_post(self):
    station = Station.objects.create(); other_station = Station.objects.create(); show = Show.objects.create(station=station, title="S", code="integrity"); other_show = Show.objects.create(station=other_station, title="Other", code="other"); ep = Episode.objects.create(show=other_show, title="E")
    with self.assertRaises(ValidationError): Occurrence(station=station, show=show, episode=ep, item_type="episode", label="x", starts_at=datetime.now(dt_timezone.utc), planned_duration_seconds=60).full_clean()
    response = Client().post("/occurrences/new/", {"item_type": "episode", "label": "tampered", "episode": ep.pk, "show": show.pk, "starts_at": "2026-01-05T12:00", "planned_duration_seconds": 60, "status": "planned"})
    assert response.status_code == 200 and not Occurrence.objects.filter(label="tampered").exists()

  def test_malformed_and_negative_expected_revision_are_rejected(self):
    station = Station.objects.create(); item = Occurrence.objects.create(station=station, item_type="filler", label="F", starts_at=datetime.now(dt_timezone.utc), planned_duration_seconds=60)
    for value in ("nope", "-1"):
      assert Client().post(f"/occurrences/{item.pk}/edit/", {"expected_revision": value}).status_code == 400

  def test_each_preparation_fact_has_independent_provenance(self):
    station = Station.objects.create(); occurrence = Occurrence.objects.create(station=station, item_type="live", label="Live", starts_at=datetime.now(dt_timezone.utc), planned_duration_seconds=60); preparation = Preparation.objects.create(occurrence=occurrence)
    confirm_preparation_fact(preparation, "ame", "preset-4", actor="owner", provenance="manual note"); preparation.refresh_from_db()
    assert preparation.ame_preset == "preset-4" and preparation.ame_actor == "owner" and preparation.ame_provenance == "manual note" and preparation.ame_at

  def test_airing_correction_links_without_erasing_original(self):
    station = Station.objects.create(); occurrence = Occurrence.objects.create(station=station, item_type="live", label="Live", starts_at=datetime.now(dt_timezone.utc), planned_duration_seconds=60)
    original = AiringEvidence.objects.create(occurrence=occurrence, status="reported", source="producer", aired_at=datetime.now(dt_timezone.utc)); correction = revise_airing(original, status="log_verified", source="log", aired_at=original.aired_at, actor="owner")
    assert AiringEvidence.objects.count() == 2 and correction.supersedes_id == original.pk and AiringEvidence.objects.get(pk=original.pk).status == "reported"
