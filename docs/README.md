# Documentation index

- `design.md`: architecture decisions for the kiosk app, including why options
  are read from `/data/options.json` directly, how touch-to-output mapping
  works without writing to the host's udev database, and the Chromium
  watchdog and profile-recovery design.
- `operations.md`: what each runtime mechanism actually does and why, for
  someone debugging the app: device access mechanics, the BusyBox `stat`
  and bashio pipefail quirks, rotation and Chromium launch flags, and the
  REST control API endpoints.
- `security.md`: trust boundaries for the app: the loopback-only control
  API, the command whitelist in `rest_server.py`, minimum device
  permissions, and AppArmor confinement.
- `decisions.md`: fixes that were tried and reverted, with the reason. See
  `wayland-kiosk/CHANGELOG.MD` for the full dated release history; this file
  covers the decisions still relevant to reading the current code.

User-facing configuration documentation (what Supervisor renders in the app
store) lives in `wayland-kiosk/DOCS.md`, not here.
