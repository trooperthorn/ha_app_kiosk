# HomeSeer Z-Wave Extensions for Home Assistant (zwave_js)

Drop-in support layer for **HomeSeer HS-WD200+** dimmers (and largely the
HS-WS200+ switch) that adds status LED control, scene/multi-tap actions and
performance tuning — **without any change to the underlying `zwave_js`
integration code**.

Everything here is built exclusively on stable, public `zwave_js` surfaces:

| Mechanism | What it gives us |
|---|---|
| `zwave_js.set_config_parameter` action | Write any device configuration parameter (LED colors, blink, status mode, ramp rates), including partial bitmask parameters |
| `zwave_js_value_notification` event | Push-based Central Scene events (single→5x taps, hold, release) with sub-100 ms latency and zero polling |
| Blueprints + script `fields` | Reusable, UI-configurable building blocks that survive HA upgrades |
| Z-Wave JS *device config priority directory* | Add/fix device definitions for new devices or firmwares by dropping a JSON file — again, no integration code changes |

## Contents

```
zwave-devices/homeseer/
├── packages/
│   └── homeseer_wd200.yaml                 # Scripts: LED color/blink/pattern, status mode, ramp rates
└── blueprints/automation/homeseer/
    ├── wd200_scene_controller.yaml         # Central Scene: 1x-5x taps, hold, release, both paddles
    └── wd200_status_led_indicator.yaml     # Bind any entity's state to a status LED
```

## Installation

1. Copy `packages/homeseer_wd200.yaml` to `/config/packages/` and enable
   packages in `configuration.yaml` (skip if you already use packages):

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Copy the two blueprint files to
   `/config/blueprints/automation/homeseer/`.

3. Restart Home Assistant (or reload scripts + automations).

## What the device supports (HS-WD200+ quick reference)

The WD200+ has a 7-LED bar with two modes selected by **parameter 13**:

- **Load Status mode (13 = 0)** — the bar shows the dim level; its color is
  **parameter 14** (0=White 1=Red 2=Green 3=Blue 4=Magenta 5=Yellow 6=Cyan).
- **Custom Status mode (13 = 1)** — each LED is individually controlled:
  - **Parameters 21–27** — color of LED 1 (bottom) … LED 7 (top):
    0=Off 1=Red 2=Green 3=Blue 4=Magenta 5=Yellow 6=Cyan 7=White
  - **Parameter 30** — blink period, n × 100 ms (0 = no blink)
  - **Parameter 31** — blink bitmask, bit 0 = LED 1 … bit 6 = LED 7

Other useful parameters: **3** (bottom LED behavior when load off),
**4** (paddle orientation), **11/12** (remote/local ramp rate).

Central Scene: the paddle reports **scene 001 (up)** and **scene 002 (down)**
with values `KeyPressed`, `KeyPressed2x` … `KeyPressed5x`, `KeyHeldDown`,
`KeyReleased`.

## Usage

### Scripts (from any automation, script, or Developer Tools → Actions)

Set the bottom LED to blinking red on every WD200 in the house:

```yaml
action: script.wd200_set_status_led
data:
  target_device:
    - abc123deviceid...        # pick in the UI; device selector supports multiple
  led: 1
  color: red
  blink: true
  blink_frequency: 3           # 300 ms
```

Paint a "gauge" (e.g. thermostat demand, timer countdown) in one call:

```yaml
action: script.wd200_set_led_pattern
data:
  target_device: ["abc123deviceid"]
  pattern: ["green", "green", "green", "yellow", "yellow", "red", "off"]
```

Return to normal dimmer behavior:

```yaml
action: script.wd200_clear_status
data:
  target_device: ["abc123deviceid"]
```

Available scripts: `wd200_set_status_led`, `wd200_set_all_status_leds`,
`wd200_set_led_pattern`, `wd200_clear_status`, `wd200_set_normal_led_color`,
`wd200_set_ramp_rates`.

### Blueprint: scene controller

Settings → Automations → Create → *HomeSeer WD200+ Scene Controller*.
Pick the device, then attach any actions to the 14 gestures
(up/down × 1x–5x tap, hold, release). Typical uses: double-tap-up = scene
"Movie", hold-down = whole-room off, triple-tap = toggle a group.

### Blueprint: status LED indicator

Settings → Automations → Create → *HomeSeer WD200+ Status LED Indicator*.
Examples:

- `binary_sensor.garage_door` state `on` → LED 7 solid red, off otherwise
- `sensor.washer` state `running,rinsing,spinning` → LED 3 blinking yellow
- `lock.front_door` state `unlocked` → LED 1 magenta

Create one automation per LED per device; they queue their Z-Wave writes so
several indicators on the same dimmer don't race.

## Performance & latency notes

- **No polling anywhere.** Device→HA state comes from the Lifeline
  association (Group 1) that Z-Wave JS configures at interview time; taps
  arrive as push Central Scene events. Avoid `zwave_js.refresh_value` in
  loops — it doubles mesh traffic and slows everything down.
- **Ramp rates (params 11/12)** control both perceived responsiveness *and*
  how quickly the final level report reaches HA (the dimmer reports when the
  ramp completes). `script.wd200_set_ramp_rates` with `0` gives instant
  on/off and the fastest state convergence; `1` keeps a subtle fade.
- **LED updates are config writes** (one Z-Wave frame per parameter). The
  scripts only touch the parameters they need, and the indicator blueprint
  runs `queued` per automation. Avoid re-painting all 7 LEDs on a fast
  sensor (e.g. power) more than ~once per few seconds to keep the mesh calm.
- **Blink frequency (param 30) is device-wide** — all blinking LEDs on one
  dimmer share it. The last writer wins.

## Extending further without touching zwave_js code

1. **Other HomeSeer devices.** The HS-WS200+ switch uses the same Central
   Scene layout and status-mode concept (single LED: params 13/14, blink 30/31),
   so the scene blueprint works as-is; the LED scripts need only the
   parameter-map tweak. The newer HS-WX300 (7 LEDs, params in the same
   family) mostly works with these scripts unchanged.
2. **New parameters / new firmware / unsupported devices.** Z-Wave JS reads
   device definitions from JSON files, and both the official add-on and
   Z-Wave JS UI support a *device config priority directory*
   (Z-Wave JS UI: Settings → Z-Wave → "Device config priority dir";
   add-on config dir mount). Drop a corrected/extended JSON there and
   re-interview the node — new config parameters appear as entities and in
   `set_config_parameter` with **zero** integration changes. Upstreaming the
   JSON to [zwave-js/node-zwave-js](https://github.com/zwave-js/node-zwave-js)
   makes it permanent for everyone.
3. **Raw command classes.** For anything not modeled as a config parameter,
   `zwave_js.invoke_cc_api` calls any Command Class API directly (e.g.
   Configuration CC `set` with explicit `valueSize`/`valueFormat` for
   parameters missing from the device DB).
4. **Entities on top.** If you want LED state as first-class entities,
   wrap the scripts with `template` select/light helpers per device — still
   pure YAML.

## Verifying it works

1. Z-Wave JS device page → the WD200+ should list config parameters 13/14/21–27/30/31.
2. Developer Tools → Actions → run `script.wd200_set_status_led` with LED 1 / red.
3. Developer Tools → Events → listen to `zwave_js_value_notification`, tap a
   paddle and confirm `command_class: 91`, `property_key: "001"/"002"`.

Manufacturer documentation: <https://docs.homeseer.com/products/hs-wd200>
