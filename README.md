# Omarchy theme scheduler

An [Omarchy](https://omarchy.org/) shell plugin that switches between a light and a dark theme automatically. The switch happens at sunrise and sunset, or at fixed times you choose. Each day you can either keep one fixed pair or let it draw a random theme from a pool.

A sun icon in the top bar means the light theme is active, and a moon means the dark one is. Click the icon to open the picker.

## Install

```bash
omarchy plugin add https://github.com/johnhaining/omarchy-theme-scheduler.git --enable --yes
omarchy plugin enable johnhaining.theme-scheduler right
```

Then click the new icon in the top-right of the bar and choose your themes. Nothing changes until you do: an unconfigured plugin does not touch your theme.

Omarchy plugins run as code inside the shell. Review `scheduler.py` and the QML before you enable it.

## The picker

- **Themes**
  - *Fixed light and dark*: one light theme and one dark theme.
  - *Random each day*: a light pool and a dark pool. At each switch a theme is drawn from the matching pool, and it stays for that period.
- **Switch at**
  - *Sunrise and sunset*: uses the location the weather widget already has. Set latitude and longitude to override it.
  - *Fixed times*: a light time and a dark time, in `HH:MM`.
- **Apply now** saves what is on screen and switches immediately. Other changes take effect at the next switch.

Themes are listed by their `colors.toml` `mode` setting. Light themes only appear for the light slot and dark themes only for the dark slot.

## How it works

- `Service.qml` runs `scheduler.py tick` every 30 seconds.
- `tick` works out the current period from the sun times or clock times. It applies a theme (`omarchy theme set <slug>`) only when the period has changed since the last switch. A theme you change by hand stays until the next switch.
- A random pick is saved per period, so it does not reroll on every tick.
- `Widget.qml` and `Panel.qml` are the bar icon and the picker. Every control saves through `scheduler.py save`.

Files:

| Path | Purpose |
|---|---|
| `~/.config/omarchy/theme-scheduler/config.json` | your settings |
| `~/.local/state/omarchy/theme-scheduler/state.json` | last switch, daily picks, last error |

## Keybinding

To flip between light and dark on demand, bind the `toggle` command in `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + L", "Toggle light/dark theme", "python3 ~/.config/omarchy/plugins/johnhaining.theme-scheduler/scheduler.py toggle")
```

Then run `hyprctl reload` and `hyprctl configerrors`. Pick any free combination; `omarchy menu keybindings --print` lists the taken ones. The toggle holds until the next sunrise or sunset, the same as the button in the picker.

## Command line

The scheduler runs on its own, but you can call it directly:

```bash
python3 ~/.config/omarchy/plugins/johnhaining.theme-scheduler/scheduler.py status
python3 ~/.config/omarchy/plugins/johnhaining.theme-scheduler/scheduler.py apply
```

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests use a temporary home directory and a fake `omarchy`, so they never change your theme. Set `OMARCHY_CMD` to point the scheduler at another command.

## Notes

- Sunrise and sunset come from the NOAA solar equations, accurate to within a couple of minutes.
- Near the poles, where the sun does not rise or set on some days, that day falls back to the fixed times.
- Light or dark is read from `mode` in each theme's `colors.toml`. A theme without that line is treated as dark.
