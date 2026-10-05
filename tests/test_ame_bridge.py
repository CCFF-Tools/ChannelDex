import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pubtv.ultranexus.ame_bridge import AMEBridge, install_plugin, prepare_bridge


class AMEBridgeTests(unittest.TestCase):
    def test_supported_uxp_job_round_trip(self):
        with TemporaryDirectory() as directory:
            root = prepare_bridge(directory)
            (root / "status.json").write_text(json.dumps({
                "ready": True, "api": "RenderQueue.renderFile",
            }), encoding="utf-8")
            source = Path(directory) / "source.mov"; source.write_bytes(b"source")
            preset = Path(directory) / "preset.epr"; preset.write_bytes(b"preset")
            output = Path(directory) / "output.mp4"
            now = [100.0]

            def sleep(_seconds):
                request_path = next((root / "requests").glob("*.json"))
                request = json.loads(request_path.read_text(encoding="utf-8"))
                self.assertEqual(request["source"], str(source.resolve()))
                self.assertEqual(request["preset"], str(preset.resolve()))
                output.write_bytes(b"encoded")
                (root / "results" / f'{request["id"]}.json').write_text(
                    json.dumps({"version": 1, "id": request["id"], "state": "succeeded"}), encoding="utf-8"
                )
                now[0] += 1

            bridge = AMEBridge(directory, clock=lambda: now[0], sleeper=sleep)
            result = bridge.render(source, preset, output, source_sha256="a" * 64,
                                   preset_sha256="b" * 64)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), b"encoded")

    def test_plugin_installs_to_stable_data_path(self):
        with TemporaryDirectory() as directory:
            manifest = install_plugin(directory)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["host"]["app"], "ame")
            self.assertEqual(payload["host"]["minVersion"], "27.0")

    def test_bundled_plugin_uses_uxp_safe_path_helpers(self):
        with TemporaryDirectory() as directory:
            install_plugin(directory)
            plugin_js = Path(directory) / "integrations" / "channeldex-ame-uxp" / "index.js"
            source = plugin_js.read_text(encoding="utf-8")
            self.assertNotIn('require("path")', source)
            self.assertNotIn("require('path')", source)
            self.assertNotIn("path.join", source)
            self.assertNotIn("path.basename", source)

    def test_ignores_terminal_result_with_wrong_protocol_or_id(self):
        with TemporaryDirectory() as directory:
            root = prepare_bridge(directory)
            (root / "status.json").write_text(json.dumps({"ready": True, "api": "RenderQueue.renderFile"}), encoding="utf-8")
            now = [100.0]
            seen = [0]

            def sleep(_seconds):
                request_path = next((root / "requests").glob("*.json"))
                request = json.loads(request_path.read_text(encoding="utf-8"))
                seen[0] += 1
                (root / "results" / f'{request["id"]}.json').write_text(json.dumps(
                    {"version": 2 if seen[0] == 1 else 1,
                     "id": "wrong" if seen[0] == 1 else request["id"], "state": "succeeded"}), encoding="utf-8")
                now[0] += 1

            result = AMEBridge(directory, timeout=3, clock=lambda: now[0], sleeper=sleep).render("source", "preset", "output")
            self.assertEqual(result.returncode, 0)

    def test_timeout_removes_request_before_a_late_panel_can_run_it(self):
        with TemporaryDirectory() as directory:
            root = prepare_bridge(directory)
            (root / "status.json").write_text(
                json.dumps({"ready": True, "api": "RenderQueue.renderFile"}), encoding="utf-8"
            )
            now = [100.0]

            def sleep(_seconds):
                now[0] += 1

            result = AMEBridge(
                directory, timeout=2, clock=lambda: now[0], sleeper=sleep
            ).render("source", "preset", "output")

            self.assertEqual(result.returncode, 1)
            self.assertFalse(list((root / "requests").iterdir()))
            self.assertFalse(list((root / "results").iterdir()))


if __name__ == "__main__":
    unittest.main()
