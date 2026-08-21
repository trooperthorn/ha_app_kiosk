# Example Automations — Z-Wave Device Support Layer

Ready-to-import recipes that compose the scripts and blueprints from the
vendor packs into real event-driven behavior: garage alerts, severe weather,
scenes, guest codes, laundry, and freeze protection.

## Installing

These are Home Assistant **packages** — copy the files you want into
`/config/packages/` (alongside the vendor packages, which they depend on),
then find-and-replace the placeholders:

| Placeholder | Replace with |
|---|---|
| `REPLACE_WD200_DEVICE_ID` | Device ID of a WD200+ dimmer (Settings → Devices → your dimmer → the `device_id` in the URL, or pick the device in any service UI and switch to YAML view) |
| `binary_sensor.garage_door` | Your garage door sensor/cover |
| `lock.front_door` | Your BE469ZP lock entity |
| `sensor.washer_power` | A power (W) sensor on the washer's Minoston plug |
| `sensor.nws_alerts` | Your weather-alert sensor (e.g. NWS Alerts) |
| `sensor.outdoor_temperature` | Your outdoor temperature sensor |
| `cover.*_shade` | Your Graber RSZ1 shade entities |

Reload YAML (Developer Tools → YAML → All) or restart after copying.

## LED assignment convention used by these examples

Give every LED a fixed meaning and keep it consistent across all dimmers, so
a glance at any switch reads the same:

| LED | Meaning | Colors used |
|---|---|---|
| 7 (top) | Security: garage / doors | red = open, blinking red = open too long |
| 6 | Weather | yellow = advisory, blinking magenta = warning |
| 5 | Lock | green flash = keypad unlock, blinking red = jammed/tamper |
| 4 | Guest mode | green = guest PIN active |
| 3 | Laundry | blinking cyan = washer done |
| 2 | Batteries | yellow < 25%, red < 10% |
| 1 (bottom) | Climate | blue = freeze protection active |

Blink is reserved for act-now events — the blink frequency (parameter 30)
is shared device-wide, so only one "urgency" can blink at a time per dimmer.

## Files

- `garage_door_alerts.yaml` — LED 7 red while open, escalates to blinking
  after 10 minutes, clears when closed
- `severe_weather_alerts.yaml` — tiered weather alerts on LED 6, warning
  level lights every dimmer; freeze protection on LED 1 + ZEN14 outlet
- `goodnight_scene.yaml` — triple-tap-down "goodnight" (lock, shades,
  outlets, vacancy mode), double-tap-up "arrive"
- `guest_code_flow.yaml` — 5x-tap programs/clears a guest PIN with LED 4
  feedback; notification on guest keypad unlock
- `laundry_done.yaml` — power-based washer-running sensor, blinking cyan
  when done, any paddle tap dismisses
- `vacation_mode.yaml` — one script each to enter/leave vacation posture
  across the lock, motion switch, shades, and plugs
- `network_health.yaml` — dead-node auto-ping (instant + 15-minute sweep)
  and a silent-sensor watchdog, per the community-proven `zwave_js.ping`
  pattern
- `alarm_panel_led.yaml` — mirrors any `alarm_control_panel` (Manual Alarm,
  Alarmo, Ring, …) onto LED 7; `triggered` lights every LED blinking red
- `actionable_notifications.yaml` — mobile_app notifications with "Lock
  now" / "Close it" buttons for an unlocked door or open garage
- `presence_and_calendar.yaml` — last-person-out lockup via `zone.home`
  (deliberately no auto-unlock on arrival), and calendar-scheduled guest
  PINs driving the guest_code_flow scripts

See `../COMMUNITY.md` for the third-party integrations these pair with
(Alarmo, NWS Alerts, KeyMaster, the WD200 status card, and more).

Most of these use plain event-trigger automations rather than the blueprints
so the whole flow is visible in one file; the blueprints remain the better
choice when you want to configure per-device instances from the UI.
