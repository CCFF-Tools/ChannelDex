from datetime import date, time

from django.test import TestCase

from pubtv.operations.episode_schedule import project_episode_schedule
from pubtv.operations.models import (
    Episode,
    Occurrence,
    RecurrenceSlot,
    Show,
    Station,
    WeeklyEpisodeAssignment,
)
from pubtv.operations.services import materialize_assignment


class EpisodeDateProjectionTests(TestCase):
    def setUp(self):
        self.station = Station.objects.create(name='PUB-TV')
        self.show = Show.objects.create(station=self.station, title='Community', code='community')
        for weekday, premiere in ((2, True), (0, False)):
            RecurrenceSlot.objects.create(station=self.station, show=self.show, weekday=weekday,
                start_time=time(19), duration_seconds=1800, is_premiere=premiere)

    def projection(self):
        return project_episode_schedule(self.show, date(2030, 1, 2), date(2030, 2, 1))

    def test_distinct_suggested_cycles_and_monday_replay_never_write_records(self):
        first = Episode.objects.create(show=self.show, title='First', intended_air_order=1)
        second = Episode.objects.create(show=self.show, title='Second', intended_air_order=2)
        rows = self.projection()['suggestions']
        dates = {(row['starts_at'].date(), row['episode'].pk) for row in rows}
        self.assertIn((date(2030, 1, 2), first.pk), dates)
        self.assertIn((date(2030, 1, 7), first.pk), dates)
        self.assertIn((date(2030, 1, 9), second.pk), dates)
        self.assertIn((date(2030, 1, 14), second.pk), dates)
        self.assertFalse(Occurrence.objects.exists())
        self.assertFalse(WeeklyEpisodeAssignment.objects.exists())

    def test_explicit_cycle_and_no_program_win_over_pending_suggestions(self):
        first = Episode.objects.create(show=self.show, title='Already planned', intended_air_order=1)
        second = Episode.objects.create(show=self.show, title='Next', intended_air_order=2)
        assignment = WeeklyEpisodeAssignment.objects.create(show=self.show, episode=first,
            week_start=date(2029, 12, 31), premiere_date=date(2030, 1, 2), selection_type='premiere')
        materialize_assignment(assignment)
        WeeklyEpisodeAssignment.objects.create(show=self.show, selection_type='none',
            week_start=date(2030, 1, 7), premiere_date=date(2030, 1, 9))
        rows = self.projection()['suggestions']
        self.assertFalse(any(row['episode'].pk == first.pk for row in rows))
        self.assertEqual(next(row['starts_at'].date() for row in rows if row['slot'].is_premiere), date(2030, 1, 16))
        self.assertTrue(all(row['episode'].pk == second.pk for row in rows))

    def test_producer_date_is_not_pulled_forward(self):
        dated = Episode.objects.create(show=self.show, title='Special', intended_air_order=1,
                                       intended_premiere_date=date(2030, 1, 23))
        undated = Episode.objects.create(show=self.show, title='Regular', intended_air_order=2)
        premieres = {row['episode'].pk: row['starts_at'].date() for row in self.projection()['suggestions'] if row['slot'].is_premiere}
        self.assertEqual(premieres[dated.pk], date(2030, 1, 23))
        self.assertEqual(premieres[undated.pk], date(2030, 1, 2))
