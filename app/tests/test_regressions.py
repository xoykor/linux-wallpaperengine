from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from wallpaper_engine_app import daemon, model


class SteamLibraryTests(unittest.TestCase):
    def test_libraryfolders_reads_new_and_legacy_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            steamapps = Path(temporary)
            (steamapps / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n'
                '{\n'
                '\t"1"\n'
                '\t{\n'
                '\t\t"path"\t\t"/mnt/Games/SteamLibrary"\n'
                '\t}\n'
                '\t"2"\t"/srv/Steam"\n'
                '}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                model._libraryfolders_paths(steamapps),
                [
                    Path("/mnt/Games/SteamLibrary/steamapps"),
                    Path("/srv/Steam/steamapps"),
                ],
            )


class DaemonRegressionTests(unittest.TestCase):
    def test_disabling_rotation_persists_current_wallpaper(self) -> None:
        service = daemon.WallpaperDaemon()
        service.config = model.validate_config({})
        service.catalog = {"123": {"title": "Current"}}
        service.current_id = "123"
        service.active = False

        with (
            mock.patch.object(service, "_refresh"),
            mock.patch.object(service, "_tick"),
            mock.patch.object(model, "save_config", side_effect=lambda value: value),
        ):
            status = service._dispatch(
                {"command": "set", "settings": {"rotation_enabled": False}}
            )

        self.assertFalse(status["config"]["rotation_enabled"])
        self.assertEqual(status["config"]["selected_id"], "123")

    def test_renderer_crash_cools_down_every_active_wallpaper(self) -> None:
        class CrashedChild:
            def poll(self) -> int:
                return 1

        service = daemon.WallpaperDaemon()
        service.config = model.validate_config({})
        service.catalog = {
            "1": {"title": "One"},
            "2": {"title": "Two"},
        }
        service.child = CrashedChild()
        service.child_group = None
        service.child_started = time.monotonic()
        service.current_id = "1"
        service.screens = {"DP-1": "1", "HDMI-A-1": "2"}

        service._reap_child()

        self.assertIn("1", service.failed)
        self.assertIn("2", service.failed)

    def test_crashing_fixed_wallpaper_falls_back_to_rotation_item(self) -> None:
        service = daemon.WallpaperDaemon()
        service.config = model.validate_config(
            {"screen_assignments": {"DP-1": "1"}}
        )
        service.outputs = ["DP-1", "HDMI-A-1"]
        service.catalog = {
            "1": {"assets": "/missing", "path": "/wallpapers/1", "title": "Bad"},
            "2": {"assets": "/missing", "path": "/wallpapers/2", "title": "Good"},
        }
        service.failed["1"] = time.time()

        with mock.patch.object(service, "_resolve_renderer", return_value="/bin/true"):
            _command, _environment, screens = service._build_command("2")

        self.assertEqual(screens["DP-1"], "2")
        self.assertEqual(screens["HDMI-A-1"], "2")


if __name__ == "__main__":
    unittest.main()
