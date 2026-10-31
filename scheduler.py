#!/usr/bin/env python3
"""Scheduled light/dark theme switching for Omarchy.

The shell plugin runs `tick` every 30 seconds, and the picker runs `save`
and `apply`. Standard library only.

  scheduler.py status          JSON for the picker: settings, themes, next switch
  scheduler.py save <json>     store settings (does not change the theme)
  scheduler.py tick            apply the current period's theme if it has changed
  scheduler.py apply [json]    save settings if given, then apply the theme now
  scheduler.py toggle          switch to the other period's theme until the next switch
  scheduler.py uninstall       delete this plugin's config and state
"""

import fcntl
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

PERIODS = ("light", "dark")
MODES = ("fixed", "random")
SCHEDULES = ("sun", "clock")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MODE_RE = re.compile(r'^\s*mode\s*=\s*"(light|dark)"', re.MULTILINE)
ZENITH = 90.833  # official sunrise/sunset, includes refraction
KEEP_PICKS = 14


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "omarchy/kyber-shift/config.json"


def state_path():
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state"
    return Path(base) / "omarchy/kyber-shift/state.json"


def theme_dirs():
    omarchy = Path(os.environ.get("OMARCHY_PATH") or "/usr/share/omarchy")
    # User themes come last so a user theme with the same slug wins.
    return [omarchy / "themes", Path.home() / ".config/omarchy/themes"]


def read_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


@contextmanager
def locked():
    """Serialize tick/apply/save so two callers never switch at once."""
    path = state_path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


# ----------------------------------------------------------------- themes


def theme_mode(path):
    try:
        text = (path / "colors.toml").read_text()
    except OSError:
        return "dark"
    match = MODE_RE.search(text)
    return match.group(1) if match else "dark"


def list_themes():
    found = {}
    for root in theme_dirs():
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.is_dir():
                found[entry.name] = entry
    return [
        {
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "mode": theme_mode(path),
        }
        for slug, path in sorted(found.items())
    ]


# ---------------------------------------------------------------- config


def coordinate(value, limit):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or abs(number) > limit:
        return None
    return number


def parse_clock(value):
    if not isinstance(value, str):
        return None
    match = TIME_RE.match(value.strip())
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def default_config(themes):
    lights = [t["slug"] for t in themes if t["mode"] == "light"]
    darks = [t["slug"] for t in themes if t["mode"] == "dark"]
    return {
        "mode": "fixed",
        "light": lights[0] if lights else "",
        "dark": darks[0] if darks else "",
        "lightPool": [],
        "darkPool": [],
        "schedule": "sun",
        "latitude": None,
        "longitude": None,
        "lightAt": "07:00",
        "darkAt": "19:00",
    }


def normalize_config(raw, themes):
    cfg = default_config(themes)
    if not isinstance(raw, dict):
        return cfg
    if raw.get("mode") in MODES:
        cfg["mode"] = raw["mode"]
    if raw.get("schedule") in SCHEDULES:
        cfg["schedule"] = raw["schedule"]
    for period in PERIODS:
        if isinstance(raw.get(period), str):
            cfg[period] = raw[period]
        pool = raw.get(period + "Pool")
        if isinstance(pool, list):
            cfg[period + "Pool"] = [s for s in pool if isinstance(s, str)]
        if parse_clock(raw.get(period + "At")):
            cfg[period + "At"] = raw[period + "At"].strip()
    cfg["latitude"] = coordinate(raw.get("latitude"), 90)
    cfg["longitude"] = coordinate(raw.get("longitude"), 180)
    return cfg


def location(cfg):
    """Coordinates from settings, else from the weather widget's saved location."""
    if cfg["latitude"] is not None and cfg["longitude"] is not None:
        return cfg["latitude"], cfg["longitude"], "settings"
    weather = read_json(Path.home() / ".local/state/omarchy/settings/weather.json") or {}
    lat = coordinate(weather.get("latitude"), 90)
    lon = coordinate(weather.get("longitude"), 180)
    if lat is not None and lon is not None:
        return lat, lon, "weather"
    return None, None, None


# ------------------------------------------------------------- sun times


def sun_event(day, lat, lon, rising):
    """NOAA sunrise equation. Returns an aware local datetime, or None in polar day/night."""
    day_of_year = day.timetuple().tm_yday
    lng_hour = lon / 15
    t = day_of_year + ((6 if rising else 18) - lng_hour) / 24
    mean_anomaly = 0.9856 * t - 3.289
    m = math.radians(mean_anomaly)
    true_longitude = (
        mean_anomaly
        + 1.916 * math.sin(m)
        + 0.020 * math.sin(2 * m)
        + 282.634
    ) % 360

    right_ascension = math.degrees(math.atan(0.91764 * math.tan(math.radians(true_longitude)))) % 360
    right_ascension += math.floor(true_longitude / 90) * 90 - math.floor(right_ascension / 90) * 90
    right_ascension /= 15

    sin_dec = 0.39782 * math.sin(math.radians(true_longitude))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(math.radians(ZENITH)) - sin_dec * math.sin(math.radians(lat))) / (
        cos_dec * math.cos(math.radians(lat))
    )
    if cos_h > 1 or cos_h < -1:
        return None

    hour_angle = math.degrees(math.acos(cos_h))
    if rising:
        hour_angle = 360 - hour_angle
    hour_angle /= 15

    local_mean = hour_angle + right_ascension - 0.06571 * t - 6.622
    ut = (local_mean - lng_hour) % 24
    midnight_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

    # The modulo can land on the wrong calendar day far from Greenwich, so
    # take the candidate that falls on `day` at the observer's longitude.
    for shift in (0, -1, 1):
        candidate = midnight_utc + timedelta(hours=ut, days=shift)
        if (candidate + timedelta(hours=lon / 15)).date() == day:
            return candidate.astimezone()
    return None


def clock_at(day, text):
    return datetime.combine(day, parse_clock(text) or time(0, 0)).astimezone()


def day_events(cfg, day, lat, lon):
    """Light and dark start times for one local date, falling back to clock times."""
    light = dark = None
    if cfg["schedule"] == "sun" and lat is not None:
        light = sun_event(day, lat, lon, rising=True)
        dark = sun_event(day, lat, lon, rising=False)
    return [
        (light or clock_at(day, cfg["lightAt"]), "light"),
        (dark or clock_at(day, cfg["darkAt"]), "dark"),
    ]


def current_period(cfg, now, lat, lon):
    """Return (kind, start, next_event) for the period containing `now`."""
    events = []
    for offset in (-2, -1, 0, 1, 2):
        events += day_events(cfg, now.date() + timedelta(days=offset), lat, lon)
    events.sort()
    past = [e for e in events if e[0] <= now]
    upcoming = [e for e in events if e[0] > now]
    if past:
        start, kind = past[-1]
    else:
        start, kind = now, "dark"
    return kind, start, (upcoming[0] if upcoming else None)


def period_key(kind, start):
    return f"{kind}:{start.date().isoformat()}"


def shown_kind(state, kind, key):
    """The period on screen: the scheduled one unless a toggle holds the other."""
    override = state.get("override") or {}
    if override.get("key") == key and override.get("kind") in PERIODS:
        return override["kind"]
    return kind


def pick_key(shown, scheduled_kind, key):
    """Random picks are kept per period; a toggled-in period gets its own."""
    return key if shown == scheduled_kind else f"{shown}:manual:{key}"


# --------------------------------------------------------------- picking


def choose_theme(cfg, kind, key, state, installed):
    """The theme for this period. Random mode rolls once per period and keeps it."""
    fixed = cfg[kind]
    if cfg["mode"] != "random":
        return fixed
    pool = [slug for slug in cfg[kind + "Pool"] if slug in installed]
    if not pool:
        return fixed
    picks = state.setdefault("picks", {})
    pick = picks.get(key)
    if pick not in pool:
        pick = random.choice(pool)
        picks[key] = pick
        while len(picks) > KEEP_PICKS:
            del picks[next(iter(picks))]
    return pick


def apply_theme(slug):
    if not slug:
        return False, "No theme chosen for this period"
    command = [os.environ.get("OMARCHY_CMD", "omarchy"), "theme", "set", slug]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if done.returncode != 0:
        return False, (done.stderr or done.stdout).strip() or f"exit {done.returncode}"
    return True, ""


# -------------------------------------------------------------- commands


def run_tick(force=False):
    with locked():
        themes = list_themes()
        raw = read_json(config_path())
        if raw is None:
            return {"result": "unconfigured"}

        cfg = normalize_config(raw, themes)
        lat, lon, _ = location(cfg)
        now = datetime.now().astimezone()
        kind, start, _ = current_period(cfg, now, lat, lon)
        key = period_key(kind, start)

        state = read_json(state_path()) or {}
        applied = state.get("applied") or {}
        if not force and applied.get("key") == key:
            return {"result": "unchanged", "theme": applied.get("theme")}

        shown = shown_kind(state, kind, key)
        installed = {t["slug"] for t in themes}
        slug = choose_theme(cfg, shown, pick_key(shown, kind, key), state, installed)
        ok, error = apply_theme(slug)
        if ok:
            state["applied"] = {"key": key, "theme": slug, "at": now.isoformat()}
        state["error"] = None if ok else error
        write_json(state_path(), state)
        return {"result": "applied" if ok else "failed", "theme": slug, "error": error}


def toggle_theme():
    """Flip the theme on screen to the other period's, held until the next switch."""
    with locked():
        themes = list_themes()
        raw = read_json(config_path())
        if raw is None:
            return {"result": "unconfigured"}

        cfg = normalize_config(raw, themes)
        lat, lon, _ = location(cfg)
        now = datetime.now().astimezone()
        kind, start, _ = current_period(cfg, now, lat, lon)
        key = period_key(kind, start)

        state = read_json(state_path()) or {}
        shown = shown_kind(state, kind, key)
        target = "dark" if shown == "light" else "light"
        installed = {t["slug"] for t in themes}
        slug = choose_theme(cfg, target, pick_key(target, kind, key), state, installed)
        ok, error = apply_theme(slug)
        if ok:
            state["override"] = {"key": key, "kind": target}
            state["applied"] = {"key": key, "theme": slug, "at": now.isoformat()}
        state["error"] = None if ok else error
        write_json(state_path(), state)
        return {
            "result": "toggled" if ok else "failed",
            "shown": target if ok else shown,
            "theme": slug,
            "error": error,
        }


def save_config(payload):
    with locked():
        themes = list_themes()
        try:
            raw = json.loads(payload)
        except ValueError:
            return {"result": "failed", "error": "Settings were not valid JSON"}
        cfg = normalize_config(raw, themes)
        write_json(config_path(), cfg)
        return {"result": "saved", "config": cfg}


def status():
    themes = list_themes()
    raw = read_json(config_path())
    cfg = normalize_config(raw, themes)
    lat, lon, source = location(cfg)
    now = datetime.now().astimezone()
    kind, start, upcoming = current_period(cfg, now, lat, lon)
    key = period_key(kind, start)

    state = read_json(state_path()) or {}
    shown = shown_kind(state, kind, key)
    scheduled = None
    if raw is not None:
        with locked():
            state = read_json(state_path()) or {}
            before = json.dumps(state, sort_keys=True)
            installed = {t["slug"] for t in themes}
            scheduled = choose_theme(cfg, shown, pick_key(shown, kind, key), state, installed)
            if json.dumps(state, sort_keys=True) != before:
                write_json(state_path(), state)

    return {
        "configured": raw is not None,
        "config": cfg,
        "themes": themes,
        "location": {"latitude": lat, "longitude": lon, "source": source},
        "period": {"kind": shown, "since": start.isoformat()},
        "manual": shown != kind,
        "next": {"kind": upcoming[1], "at": upcoming[0].isoformat()} if upcoming else None,
        "today": {kind_name: when.isoformat() for when, kind_name in day_events(cfg, now.date(), lat, lon)},
        "scheduled": scheduled,
        "applied": state.get("applied"),
        "error": state.get("error"),
    }


def apply_now(payload=None):
    """Apply the current period's theme now, saving `payload` first if given."""
    if payload is not None:
        saved = save_config(payload)
        if saved["result"] != "saved":
            return saved
    return run_tick(force=True)


def uninstall():
    """Delete this plugin's config and state. Run before `omarchy plugin remove`."""
    removed = []
    for path in (config_path().parent, state_path().parent):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(str(path))
    return {"result": "removed", "paths": removed}


def main(argv):
    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        output = status()
    elif command == "save" and len(argv) > 2:
        output = save_config(argv[2])
    elif command == "tick":
        output = run_tick()
    elif command == "toggle":
        output = toggle_theme()
    elif command == "uninstall":
        output = uninstall()
    elif command == "apply":
        output = apply_now(argv[2] if len(argv) > 2 else None)
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
