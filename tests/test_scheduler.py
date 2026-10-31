import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scheduler  # noqa: E402


def make_theme(root, slug, mode):
    path = root / "themes" / slug
    path.mkdir(parents=True)
    (path / "colors.toml").write_text(f'mode = "{mode}"\nbackground = "#000000"\n')


class SandboxCase(unittest.TestCase):
    """Runs each test with HOME, XDG dirs, themes, and `omarchy` all pointed at a temp dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.omarchy_log = self.tmp / "omarchy.log"
        fake = self.tmp / "fake-omarchy"
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{self.omarchy_log}"\n')
        fake.chmod(0o755)

        make_theme(self.tmp / "omarchy", "tokyo-night", "dark")
        make_theme(self.tmp / "omarchy", "rose-pine", "dark")
        make_theme(self.tmp / "omarchy", "catppuccin-latte", "light")
        make_theme(self.tmp / "omarchy", "flexoki-light", "light")

        self.env = {
            "HOME": str(self.tmp / "home"),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_STATE_HOME": str(self.tmp / "state"),
            "OMARCHY_PATH": str(self.tmp / "omarchy"),
            "OMARCHY_CMD": str(fake),
        }
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_config(self, **overrides):
        cfg = {
            "mode": "fixed",
            "light": "catppuccin-latte",
            "dark": "tokyo-night",
            "schedule": "clock",
            "lightAt": "07:00",
            "darkAt": "19:00",
        }
        cfg.update(overrides)
        scheduler.write_json(scheduler.config_path(), cfg)

    def applied_themes(self):
        if not self.omarchy_log.exists():
            return []
        return [line.split()[-1] for line in self.omarchy_log.read_text().splitlines()]


class SunTimeTests(unittest.TestCase):
    def test_new_york_midsummer_matches_published_values(self):
        # NOAA: 2024-06-21 sunrise 5:25 EDT, sunset 8:31 EDT (00:31 UTC on the 22nd).
        day = date(2024, 6, 21)
        rise = scheduler.sun_event(day, 40.7128, -74.0060, rising=True)
        sett = scheduler.sun_event(day, 40.7128, -74.0060, rising=False)
        self.assertEqual(rise.astimezone(timezone.utc).strftime("%H:%M"), "09:25")
        self.assertLess(abs((sett.astimezone(timezone.utc) - datetime(2024, 6, 22, 0, 31, tzinfo=timezone.utc)).total_seconds()), 300)
        nyc = timezone(timedelta(hours=-4))
        self.assertEqual(sett.astimezone(nyc).date(), day)

    def test_abu_dhabi_late_october_is_plausible(self):
        day = date(2026, 10, 31)
        rise = scheduler.sun_event(day, 24.4539, 54.3773, rising=True).astimezone()
        sett = scheduler.sun_event(day, 24.4539, 54.3773, rising=False).astimezone()
        # Abu Dhabi (UTC+4) late October: sunrise about 06:10, sunset about 17:30.
        self.assertEqual(rise.hour, 6)
        self.assertIn(sett.hour, (17, 18))

    def test_polar_day_has_no_sunset(self):
        self.assertIsNone(scheduler.sun_event(date(2024, 6, 21), 78.0, 15.0, rising=False))


class PeriodTests(SandboxCase):
    def test_clock_schedule_switches_at_configured_times(self):
        cfg = scheduler.normalize_config({"schedule": "clock", "lightAt": "07:00", "darkAt": "19:00"}, [])
        morning = datetime(2026, 10, 31, 8, 0).astimezone()
        evening = datetime(2026, 10, 31, 20, 0).astimezone()
        kind, start, nxt = scheduler.current_period(cfg, morning, None, None)
        self.assertEqual((kind, start.hour), ("light", 7))
        self.assertEqual(nxt[1], "dark")
        kind, start, _ = scheduler.current_period(cfg, evening, None, None)
        self.assertEqual((kind, start.hour), ("dark", 19))

    def test_before_first_light_is_still_the_previous_dark_period(self):
        cfg = scheduler.normalize_config({"schedule": "clock", "lightAt": "07:00", "darkAt": "19:00"}, [])
        before = datetime(2026, 10, 31, 3, 0).astimezone()
        kind, start, _ = scheduler.current_period(cfg, before, None, None)
        self.assertEqual(kind, "dark")
        self.assertEqual(start.date(), date(2026, 10, 30))

    def test_sun_schedule_uses_sunrise_and_sunset(self):
        cfg = scheduler.normalize_config({"schedule": "sun"}, [])
        lat, lon = 40.7128, -74.0060
        noon_nyc = datetime(2024, 6, 21, 12, 0, tzinfo=timezone(timedelta(hours=-4)))
        kind, _, nxt = scheduler.current_period(cfg, noon_nyc.astimezone(), lat, lon)
        self.assertEqual(kind, "light")
        self.assertEqual(nxt[1], "dark")
        self.assertEqual(nxt[0].astimezone(timezone.utc).strftime("%H"), "00")


class TickTests(SandboxCase):
    def test_unconfigured_does_nothing(self):
        self.assertEqual(scheduler.run_tick(), {"result": "unconfigured"})
        self.assertEqual(self.applied_themes(), [])

    def test_first_tick_applies_then_stays_quiet_until_the_period_changes(self):
        self.write_config()
        with mock.patch.object(scheduler, "datetime", wraps=datetime) as fake:
            fake.now.return_value = datetime(2026, 10, 31, 8, 0).astimezone()
            first = scheduler.run_tick()
            second = scheduler.run_tick()
        self.assertEqual(first["result"], "applied")
        self.assertEqual(first["theme"], "catppuccin-latte")
        self.assertEqual(second["result"], "unchanged")
        self.assertEqual(self.applied_themes(), ["catppuccin-latte"])

    def test_manual_theme_change_is_respected_until_next_switch(self):
        self.write_config()
        with mock.patch.object(scheduler, "datetime", wraps=datetime) as fake:
            fake.now.return_value = datetime(2026, 10, 31, 8, 0).astimezone()
            scheduler.run_tick()
            fake.now.return_value = datetime(2026, 10, 31, 20, 0).astimezone()
            result = scheduler.run_tick()
        self.assertEqual(result["theme"], "tokyo-night")
        self.assertEqual(self.applied_themes(), ["catppuccin-latte", "tokyo-night"])

    def test_apply_forces_the_current_period_theme(self):
        self.write_config()
        scheduler.run_tick()
        result = scheduler.run_tick(force=True)
        self.assertEqual(result["result"], "applied")
        self.assertEqual(len(self.applied_themes()), 2)

    def test_failed_apply_is_reported_and_retried(self):
        self.write_config()
        os.environ["OMARCHY_CMD"] = "/bin/false"
        self.addCleanup(os.environ.__setitem__, "OMARCHY_CMD", self.env["OMARCHY_CMD"])
        result = scheduler.run_tick()
        self.assertEqual(result["result"], "failed")
        self.assertIsNone(scheduler.read_json(scheduler.state_path()).get("applied"))


class RandomModeTests(SandboxCase):
    def test_pick_is_stable_within_a_period_and_comes_from_the_pool(self):
        cfg = scheduler.normalize_config(
            {"mode": "random", "lightPool": ["catppuccin-latte", "flexoki-light"], "light": "catppuccin-latte"},
            [],
        )
        state = {}
        installed = {"catppuccin-latte", "flexoki-light"}
        first = scheduler.choose_theme(cfg, "light", "light:2026-10-31", state, installed)
        again = scheduler.choose_theme(cfg, "light", "light:2026-10-31", state, installed)
        self.assertIn(first, cfg["lightPool"])
        self.assertEqual(first, again)

    def test_new_day_rerolls_and_empty_pool_falls_back_to_fixed(self):
        cfg = scheduler.normalize_config({"mode": "random", "dark": "tokyo-night", "darkPool": []}, [])
        state = {}
        self.assertEqual(scheduler.choose_theme(cfg, "dark", "dark:2026-10-31", state, {"tokyo-night"}), "tokyo-night")

        cfg["darkPool"] = ["rose-pine", "tokyo-night"]
        seen = set()
        with mock.patch.object(scheduler.random, "choice", side_effect=["rose-pine", "tokyo-night"]):
            seen.add(scheduler.choose_theme(cfg, "dark", "dark:2026-10-31", state, {"rose-pine", "tokyo-night"}))
            seen.add(scheduler.choose_theme(cfg, "dark", "dark:2026-11-01", state, {"rose-pine", "tokyo-night"}))
        self.assertEqual(seen, {"rose-pine", "tokyo-night"})

    def test_uninstalled_pool_entries_are_ignored(self):
        cfg = scheduler.normalize_config({"mode": "random", "darkPool": ["gone"], "dark": "tokyo-night"}, [])
        self.assertEqual(scheduler.choose_theme(cfg, "dark", "k", {}, {"tokyo-night"}), "tokyo-night")


class ConfigTests(SandboxCase):
    def test_save_normalizes_and_rejects_bad_values(self):
        result = scheduler.save_config(json.dumps({
            "mode": "sometimes",
            "schedule": "sun",
            "latitude": 999,
            "longitude": "54.3773",
            "lightAt": "7am",
            "darkAt": "19:30",
        }))
        cfg = result["config"]
        self.assertEqual(cfg["mode"], "fixed")
        self.assertIsNone(cfg["latitude"])
        self.assertEqual(cfg["longitude"], 54.3773)
        self.assertEqual(cfg["darkAt"], "19:30")
        self.assertEqual(cfg["lightAt"], "07:00")
        self.assertEqual(scheduler.read_json(scheduler.config_path())["darkAt"], "19:30")

    def test_location_falls_back_to_weather_widget_state(self):
        weather = Path(self.env["HOME"]) / ".local/state/omarchy/settings/weather.json"
        weather.parent.mkdir(parents=True)
        weather.write_text(json.dumps({"name": "x", "latitude": 24.4539, "longitude": 54.3773}))
        cfg = scheduler.normalize_config({}, [])
        self.assertEqual(scheduler.location(cfg), (24.4539, 54.3773, "weather"))

    def test_status_lists_themes_with_modes(self):
        self.write_config()
        result = scheduler.status()
        modes = {t["slug"]: t["mode"] for t in result["themes"]}
        self.assertEqual(modes["catppuccin-latte"], "light")
        self.assertEqual(modes["tokyo-night"], "dark")
        self.assertTrue(result["configured"])
        self.assertIn("next", result)


if __name__ == "__main__":
    unittest.main()
