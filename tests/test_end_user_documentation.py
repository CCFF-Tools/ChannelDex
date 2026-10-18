from pathlib import Path

from django.test import SimpleTestCase

from pubtv.operations.models import AiringEvidence


class EndUserDocumentationTests(SimpleTestCase):
    def test_help_renders_named_links_for_primary_workflows(self):
        response = self.client.get("/help/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for href in ("/settings/", "/settings/devices/", "/library/", "/", "/schedule/?mode=day", "/schedule/?mode=week", "/schedule/?mode=agenda", "/occurrences/new/", "/schedule/recurring/", "/intake/", "/media/preparation/", "/schedule/publish/", "/history/"):
            self.assertIn(f'href="{href}"', html)
        self.assertIn("Library contains shows", html)
        self.assertIn("do not imply that an item aired", html)
        self.assertIn("Preparation uses configured tools", html)
        airing_labels = [label for _, label in AiringEvidence._meta.get_field("status").choices]
        self.assertEqual(airing_labels, ["Reported/observed", "Log verified"])
        self.assertNotIn("Choose exactly one status: <strong>Observed</strong>", html)
        self.assertIn("History</a> is a read-only record", html)

    def test_readme_covers_settings_formats_and_schedule_boundaries(self):
        readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
        for wording in (
            "America/Detroit",
            "2026-10-07T19:00",
            "/internal/schedule/schedule.bin",
            "/Vol1/mpeg",
            "carry-forward",
            "does not shift, trim, add filler",
            "qualification-gated",
            "Aired date and",
            "Destination device",
            "Source BIN slot",
            "observed_active",
            "Episode title",
            "disabled `Asset ID`",
        ):
            self.assertIn(wording, readme)
