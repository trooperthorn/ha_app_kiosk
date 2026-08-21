# Community Resources — Z-Wave in Home Assistant

Curated third-party integrations, blueprints, and cards that complement (or
supersede) the packs in this directory. Everything here works alongside the
stock `zwave_js` integration.

## Lock management

- **[KeyMaster](https://github.com/FutureTense/keymaster)** (HACS) — the
  de-facto standard for Z-Wave lock code management: per-slot dashboards,
  schedules (day/time windows), date-range access, limited-use PINs, and
  notifications. **This is the upgrade path** from our
  `schlage/` scripts when you need scheduled or limited guest access on the
  BE469ZP. Our scripts remain the lighter choice for simple set/clear flows.
- **[Lock Code Manager](https://github.com/raman325/lock_code_manager)**
  (HACS) — newer, lighter alternative by a zwave-js maintainer; manages the
  same slot across multiple locks and protocols (Z-Wave, Matter, MQTT).
- **[Schlage Lock User Code Actions blueprint](https://community.home-assistant.io/t/zwave-js-schlage-lock-user-code-actions/280538)**
  — community equivalent of our `be469_lock_event_actions.yaml`.

## HomeSeer WD200+

- **[Lovelace HomeSeer WD200+ Status Card](https://community.home-assistant.io/t/lovelace-homeseer-wd200-status-card/322842)**
  (rodpayne, [repo](https://github.com/rodpayne/home-assistant)) — dashboard
  card mirroring the 7 status LEDs' color/blink state in the UI (WX300
  supported too). Pairs with our LED scripts so the wall dimmer and a kiosk
  dashboard show the same status.

## Network health / supportability

- **[Automate ZwaveJS Ping Dead Nodes](https://community.home-assistant.io/t/automate-zwavejs-ping-dead-nodes/374307)**
  — the long-running community thread behind our
  `examples/network_health.yaml`; a `zwave_js.ping` usually revives a dead
  node without a restart.
- **[Ping to detect dead battery nodes](https://community.home-assistant.io/t/using-z-wave-js-ping-to-detect-dead-battery-powered-nodes/342319)**
  — battery-device variant and the "expected data" watchdog idea.

## Scene controllers & switches

- **[GE/Jasco/Honeywell Double Taps blueprint](https://community.home-assistant.io/t/zwave-js-ge-jasco-honeywell-double-taps/280891)**
  — double-tap events on GE paddles; if your 26931 firmware emits Central
  Scene, our WD200 scene blueprint's trigger pattern works there too.
- **[ZEN32 Scene Controller blueprint](https://community.home-assistant.io/t/zen32-scene-controller-z-wave-js/292610)**
  and **[gregmac's Zooz blueprint collection](https://github.com/gregmac/HomeAssistant-ZoozBlueprints)**
  — button + LED-indicator patterns for Zooz gear; the ZEN32 makes a good
  dedicated scene keypad next to the WD200s.
- **[Awesome HA Blueprints](https://github.com/lsismeiro/awesome-ha-blueprints)**
  — curated index; its "controller + hooks" architecture is the most
  scalable community pattern for many-device setups.

## Cross-integration companions (used by our examples/)

- **[Alarmo](https://github.com/nielsfaber/alarmo)** (HACS) — full alarm
  panel built from your existing Z-Wave sensors; drives
  `examples/alarm_panel_led.yaml` (any `alarm_control_panel` entity works).
- **[NWS Alerts](https://github.com/finity69x2/nws_alerts)** (HACS) — the
  weather-alert sensor used by `examples/severe_weather_alerts.yaml`.
- **Companion app / `mobile_app`** (built-in) — actionable notifications for
  `examples/actionable_notifications.yaml`
  ([docs](https://companion.home-assistant.io/docs/notifications/actionable-notifications/)).
- **Local Calendar** (built-in) — drives calendar-scheduled guest PINs in
  `examples/presence_and_calendar.yaml`.
- **[Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting)**
  (HACS) — circadian brightness/color for dimmers; works with the WD200's
  light entity (leave its ramp rates gentle if you use gradual transitions).
