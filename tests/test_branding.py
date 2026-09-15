from pathlib import Path
import unittest


class BrandingAssetsTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_base_uses_channel_dex_lockup_and_favicon(self):
        base = (self.root / "pubtv" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("channeldex-full-logo-color.png", base)
        self.assertIn("channeldex-favicon.png", base)
        self.assertIn("Quit ChannelDex", base)

    def test_required_local_fonts_are_bundled(self):
        fonts = self.root / "pubtv" / "static" / "fonts"
        self.assertTrue((fonts / "IBMPlexSans-VariableFont_wdth,wght.ttf").exists())
        self.assertTrue((fonts / "IBMPlexMono-Regular.ttf").exists())
        self.assertTrue((fonts / "IBMPlexMono-SemiBold.ttf").exists())

    def test_macos_app_icon_is_icns(self):
        icon = self.root / "pubtv" / "static" / "images" / "channeldex-app-icon.icns"
        self.assertEqual(icon.read_bytes()[:4], b"icns")

    def test_dashboard_landing_uses_supplied_tagline_lockup_and_setup_cta(self):
        dashboard = (self.root / "pubtv" / "templates" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("channeldex-full-logo-color-tagline.png", dashboard)
        self.assertIn("ChannelDex – Television Programming Management", dashboard)
        self.assertIn("{% url 'setup-station' %}", dashboard)
        self.assertIn('class="landing-hero"', dashboard)

    def test_configured_dashboard_exposes_core_workflow_links(self):
        dashboard = (self.root / "pubtv" / "templates" / "dashboard.html").read_text(encoding="utf-8")
        for url_name in ("day-view", "assignment-create", "show-list", "history"):
            self.assertIn("{% url '" + url_name + "' %}", dashboard)
        self.assertIn('class="workflow-grid"', dashboard)

    def test_dashboard_landing_asset_is_copied_without_substitution(self):
        source = self.root / "creative" / "Finished Creative Assets" / "PNGs" / "ChannelDex Full Logo Color with Tagline.png"
        copied = self.root / "pubtv" / "static" / "images" / "channeldex-full-logo-color-tagline.png"
        self.assertTrue(copied.exists())
        self.assertEqual(source.read_bytes(), copied.read_bytes())
