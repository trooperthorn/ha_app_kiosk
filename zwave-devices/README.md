# Z-Wave Device Support Layer for Home Assistant (zwave_js)

Vendor-organized packages and blueprints that extend device supportability on
top of the stock `zwave_js` integration — **no integration code changes**.
Everything uses public `zwave_js` services (`set_config_parameter`,
`set_lock_usercode`, …) and push events (`zwave_js_value_notification`,
`zwave_js_notification`), so it survives Home Assistant upgrades.

## Supported devices

| Directory | Device(s) | What you get |
|---|---|---|
| `homeseer/` | HS-WD200+ dimmer (WS200+/WX300 mostly compatible) | Status LED color/blink/pattern scripts, Central Scene 1x–5x tap blueprint, entity→LED indicator blueprint, ramp-rate tuning |
| `schlage/` | BE469ZP Connect deadbolt | User-code set/clear scripts, built-in alarm mode + sensitivity, beeper/vacation/auto-lock options, lock-event blueprint (keypad unlock per slot, jammed, tamper…) |
| `graber/` | RSZ1 roller shade (Springs Window Fashions) | Group position + preset scripts, low-battery monitor blueprint |
| `minoston/` | MP21Z / MP31Z mini plugs | LED indicator mode, auto on/off timer scripts |
| `zooz/` | ZEN14 outdoor double plug | Per-outlet auto on/off timers, LED mode + brightness, power-failure recovery, physical-button lockout |
| `ge-jasco/` | 26931 / ZW4006 Enbrighten motion switch | Occupancy/vacancy/manual mode + timeout scripts, PIR sensitivity/light-sensing config, day/night mode-schedule blueprint |

## Installation (same pattern for every vendor)

1. Copy the files under `<vendor>/packages/` to `/config/packages/` and enable
   packages once in `configuration.yaml`:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Copy any `<vendor>/blueprints/automation/*.yaml` you want to
   `/config/blueprints/automation/<vendor>/`.

3. Restart Home Assistant (or reload scripts + automations).

`COMMUNITY.md` indexes complementary third-party projects — notably
[KeyMaster](https://github.com/FutureTense/keymaster) as the upgrade path
from the `schlage/` scripts when you need scheduled, date-ranged, or
limited-use lock PINs, and the WD200+ status card for dashboards.

Each vendor directory documents its device's configuration-parameter map at
the top of the package file. The HomeSeer README additionally covers the
general architecture, latency tuning, and how to extend *unsupported*
devices via Z-Wave JS's device-config priority directory.

## Design rules (apply to every pack)

- **Push, never poll.** State comes from Lifeline associations and
  notification events. `zwave_js.refresh_value` is a last resort.
- **Scripts are UI-first.** Every script exposes typed `fields` with device
  pickers filtered to the right manufacturer, so they are usable from the
  Actions UI, automations, and dashboards without editing YAML. If a
  manufacturer-name filter doesn't match your firmware's branding, the
  picker just shows fewer suggestions — you can still select any device.
- **Parameter writes are minimal.** Only the parameters you set are touched;
  optional fields left empty are skipped.
- **Multi-device targets.** All scripts accept multiple devices/entities and
  fan out in one call.
