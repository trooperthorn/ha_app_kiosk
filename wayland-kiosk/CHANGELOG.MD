# Changelog
## KNOWN ISSUES
- Install slower to add chrome, 1-5 minutes. I can't fix that.

## 2026.09.03.1
- Migrate build.yaml to the Dockerfile (build.yaml is retired since the
  2026-04-02 builder migration); BUILD_FROM stays on the generic
  `base:latest` tag, which tracks Alpine edge -- the Cage build needs
  wlroots0.20-dev, which does not exist on a pinned stable Alpine branch.
- Fix: aiohttp installed via pip instead of the apk py3-aiohttp package,
  which was pinned to a version with a published High-severity fix.
  Dropped py3-requests, which was never imported.
- Add apk upgrade before installing packages so the image picks up current
  Alpine security patches.
- Add CI: Test, Validate, and Security workflows (syntax/lint, Dockerfile
  lint, CodeQL, bandit, ShellCheck, and a Grype image scan scoped to
  findings with an available fix), plus a Release workflow that tags and
  publishes a GitHub Release on merge to main.
- Move long code comments into docs/ (design, operations, security,
  decisions); see docs/README.md.

## 2026.09.01.4
- Fix: prevent Chromium watchdog false-positive restarts. CDP commands are
  serialized, renderer checks have a 15-second deadline, a page reload is
  attempted before restart, and a final 30-second probe must also fail before
  Cage is terminated. Expected CDP timeouts no longer dump tracebacks.

## 2026.09.01.3
- Add the Mesa Gallium VA-API driver so AMD systems can hardware-decode
  Home Assistant camera streams through Chromium instead of falling back to
  CPU decoding. Startup now probes the render node with `vainfo` and logs
  the detected driver or an explicit CPU-fallback warning.
- Rename and document `api_token` in the UI as the kiosk control API token.
  It is an app-local shared secret, not a Home Assistant long-lived access
  token, and is now rendered as a password field.
- Harden the Chromium watchdog against false-positive restarts. CDP commands
  are serialized, renderer checks have a 15-second deadline, a page reload
  is attempted before restart, and a final 30-second probe must also fail
  before Cage is terminated. Expected CDP timeouts no longer dump tracebacks.

## 2026.09.01.2
- Fix: remove accidental zero destination blob IDs from the bundled Cage
  patch. GNU `patch` interpreted those Git metadata lines as file deletions,
  applied the hunks, then exited with status 1 before Meson could run.

## 2026.09.01.1
- Fix: load validated add-on options directly from `/data/options.json`.
  Startup no longer calls the Supervisor options API, receives repeated
  403 responses, loses non-default settings, or waits five minutes before
  applying rotation.
- Fix: apply the output transform from Cage's launcher before Chromium
  creates its Wayland surface. This avoids the delayed landscape-to-portrait
  resize which coincided with a renderer SIGTRAP in field testing.
- Fix: Chromium now launches with `--app=<url>`, `--kiosk`, and
  `--start-fullscreen`. App mode removes the tab strip and address bar even
  when Chromium's early Wayland fullscreen request remains maximized.
- Fix: build a pinned Cage 0.3.1 with a small compositor patch which maps
  input devices lacking a `WL_OUTPUT` property to the auto-discovered DRM
  connector. This fixes ILITEK touch panels logging `cannot be mapped to an
  output device` and allows output rotation to transform touch coordinates.
- Fix: the REST server now starts with the compositor's `WAYLAND_DISPLAY`,
  `launch_url` actually navigates through CDP, and `display_on` no longer
  waits on a long-running swayidle process until timeout.
- Implemented the previously inert `browser_refresh` option.
- Added per-architecture build bases and documented the trusted-proxy versus
  trusted-networks loopback conflict discovered during authentication tests.

## 2026.08.30.1
- Fix (second attempt, the sed-based one did not hold in the field): tab
  stacking from crash-session restore. The profile's exit state is now
  patched with a real JSON edit via python3 (immune to formatting
  differences that sed missed), `session.restore_on_startup` is pinned to
  the New Tab page, and the saved tab-session snapshots
  (`Default/Sessions`) are deleted before every launch -- a kiosk never
  wants tab restore, and those files are what stacked tabs are rebuilt
  from. Login cookies and Local Storage (Browser Mod ID) are untouched.
- New: the add-on now logs the browser's actual open pages (the "address
  bar" contents) at ~30s and ~90s after launch, via the DevTools target
  list -- so stacked tabs, redirects, and login pages can be diagnosed
  from the add-on log without photographing the screen. A healthy boot
  reports exactly one page.
- DOCS: added the working `auth_providers` example for trusted_networks
  auto-login. Provider ORDER matters: `trusted_networks` must be listed
  before `homeassistant`, or the login screen serves the password form by
  default and never bypasses. Requires a full Core restart after editing.

## 2026.08.24.2
- Boot ordering: run.sh now waits (up to 5 minutes) for `ha_url` to serve
  an HTTP response before launching the browser. At a cold boot, Core is
  launched before this add-on but often is not serving yet, and Chromium
  never retries a failed connection on its own, so a host reboot used to
  park the kiosk on a dead "connection refused" page.
- Fix: the script-level `screen_timeout` fallback was 600 while the
  documented default (config.yaml) is 0, so any boot with the Supervisor
  API down started blanking the screen after 10 minutes. Fallback now
  mirrors the default (0, disabled), same principle as the rotate_display
  fallback fix in 2026.08.24.1.
- Wired the `ha_dashboard` option into the loaded URL (it was accepted by
  the schema but read nowhere). Default `lovelace` lands the kiosk on
  Home Assistant's Overview dashboard.
- Exported HOME=/root: Chromium expects HOME for its NSS certificate
  store, and the chromium-browser wrapper retained as the launch fallback
  checks `stat -c %u -L ${XDG_CONFIG_HOME:-${HOME}}`; with HOME unset that
  ran with no file argument (the actual trigger for the BusyBox usage-text
  dump) and its `[ $(...) -eq 0 ]` test collapsed to the
  "sh: 0: unknown operand" error. The fallback path is now clean too.
- DOCS: added an honest option-status table (browser_refresh, ha_sidebar,
  ha_theme, and dark_mode are accepted by the schema but not applied yet),
  a boot/reboot behavior section (Start on boot + Watchdog toggles, the
  new HTTP wait), and a Browser Mod section with a worked camera-popup
  automation example.

## 2026.08.24.1
- Fix: `rotate_display` falling back to `normal` on a boot-time Supervisor
  API outage, even though this kiosk's actual default is `right`
  (config.yaml's `options.rotate_display`). The script-level fallback in
  run.sh hardcoded "normal" separately from that default -- changed it to
  "right" so an outage never silently un-rotates the display.
- Fix: the BusyBox `stat` usage/help text dumping into the log on every
  boot, immediately followed by `sh: 0: unknown operand`. Root cause
  (verified against Alpine's aports APKBUILD): `chromium` and
  `chromium-browser` on PATH are both symlinks to the same
  `chromium-launcher.sh` wrapper, whose root-user safety check
  (`stat -c %u -L "$HOME"`) uses a `-L` flag this image's BusyBox `stat`
  applet doesn't support. Chromium is now launched directly at
  `/usr/lib/chromium/chromium` (the exact path the wrapper itself resolves
  to and execs), skipping the wrapper and its broken check entirely --
  with a runtime fallback to `chromium-browser` if that path is ever
  missing, so a future package layout change degrades to log spam rather
  than failing to start.
- Fix (log-spam reduction): the rotation and screen-timeout recovery
  polling (added in 2026.08.20.4/.5) ran as two fully independent loops,
  each calling `bashio::config` on its own schedule -- roughly doubling
  the "Unable to access the API, forbidden" lines bashio itself logs on
  every failed call during a long Supervisor outage. Note: `2>/dev/null`
  on those calls never actually suppressed bashio's own log output --
  bashio's log functions (lib/log.sh) write straight to a file descriptor
  saved before any redirection happens, so there was never a way to
  silence them from here, only to make fewer calls. Replaced both loops
  with ONE shared poller with an initial 30s delay and backoff
  (15/15/30/30/60/60/60s, ~5 minutes total) instead of a flat 5s interval
  -- about 8 total calls across a bad outage instead of up to 120.
- Confirmed benign, left as-is: the dbus "Failed to connect to the bus"
  lines (no D-Bus daemon in this container; Chromium degrades gracefully)
  and the Vulkan/libvk_swiftshader warnings (no software Vulkan renderer
  installed; irrelevant since GPU rendering via amdgpu/EGL already works).

## 2026.08.23.2
- Fix: Chromium restored previous-session tabs on top of the kiosk URL
  (URL visible in multiple tabs, address bar showing -- i.e. kiosk
  presentation broken). Cause: the persistent profile added earlier means
  Chromium remembers its session, and a kiosk container never exits
  cleanly, so every boot looked like a crash recovery and stacked one
  more tab. run.sh now rewrites the profile's exit state to "clean"
  before every launch and adds `--disable-session-crashed-bubble` /
  `--hide-crash-restore-bubble`. If tabs already accumulated from before
  this fix, one more restart clears them.

## 2026.08.23.1
- Silenced Chromium's GCM phone-home log spam ("Registration response
  error message: DEPRECATED_ENDPOINT / PHONE_REGISTRATION_ERROR") at the
  source with `--disable-background-networking`,
  `--disable-component-update` and `--disable-features=GCM` (same fix as
  chromiumembedded/cef#4078). These services are useless on a kiosk.
  Deliberately did NOT add `--log-level=3` -- that only hides ERROR
  output, and ERROR lines have repeatedly been the diagnostic signal for
  real bugs in this add-on.
- Added `--disable-dev-shm-usage`: Docker's default 64MB /dev/shm can be
  exceeded by Chromium rendering a large dashboard, crashing tabs; this
  makes Chromium use /tmp instead.

## 2026.08.20.5
- Extended the Supervisor-API recovery polling for rotation and
  screen-timeout from ~60s to ~5 minutes: the startup window in which the
  Supervisor rejects the add-on's token (403) has been observed to exceed
  one minute in the field, and applying rotation late beats never.
- FAQ, verified: the API "forbidden" errors are NOT a wrong port (bashio
  talks to `http://supervisor/`, injected by Supervisor -- a 403 means the
  request arrived and was answered) and are NOT affected by Protection
  Mode (Supervisor validates the token before any privilege/role check,
  so disabling protection changes nothing about them).

## 2026.08.20.4
- Fix: rotation and screen-timeout now recover when the Supervisor API
  rejects the add-on token during the startup window (403 at boot, 200
  shortly after -- confirmed in the field with a direct curl test). The
  rotation and swayidle background tasks re-read their option after the
  compositor is up, polling up to ~60s for the API to recover, instead of
  being stuck with boot-time fallback values (`rotate_display` "normal",
  `screen_timeout` 600) for the whole run.
- Fix: `wlr-randr --output *` is not valid -- wlr-randr has no wildcard
  support and fails with "unknown output *" (seen in the field when the
  swayidle timeout fired). Screen blanking and the REST `display_on` /
  `display_off` endpoints therefore never worked. All of them now target
  the discovered connector name, passed to rest_server.py via the
  KIOSK_OUTPUT environment variable.
- Added the `xwayland` package so Cage stops logging "Cannot find Xwayland
  binary /usr/bin/Xwayland" at every start (Chromium runs native Wayland;
  this only silences the error and covers X11 fallback paths).

## 2026.08.20.3
- **Fix (root cause of "Found 0 GPUs" / EPERM on /dev/dri)**: the
  `devices:` list used the legacy Docker triplet syntax on directories
  (`/dev/dri:/dev/dri:rwm`). Modern Supervisor resolves each entry against
  its udev hardware database and SILENTLY skips anything that isn't an
  existing individual device node -- so the add-on ran with zero device
  grants and every open() of /dev/dri/card0 failed with "Operation not
  permitted" from the cgroup v2 device filter (verified in Supervisor
  source: docker/app.py cgroups_rules -> hardware get_by_path ->
  HardwareNotFound -> debug-level skip). Replaced with explicit per-node
  paths (/dev/dri/card0, card1, renderD128/129, /dev/input/event0-25),
  the same proven format the HAOS-kiosk X11 add-on uses; nonexistent
  nodes are skipped harmlessly.
- Removed the udev touch-calibration block from run.sh entirely. With
  `udev: true`, Supervisor bind-mounts the HOST's /run/udev read-only, so
  container-written udev rules are invisible to the host udevd and
  `udevadm control` can only hang (the "Terminated timeout 5 udevadm" log
  line). It is also unnecessary under Wayland: wlroots applies the output
  transform to touchscreen input automatically, so `wlr-randr --transform`
  rotates display and touch together.

## 2026.08.20.2
- **Revert**: removed `apparmor: false` (added in 2026.08.20.1). Confirmed
  by a field log that it broke `/dev/dri` device cgroup access (Supervisor
  ties cgroup device rules to the AppArmor profile), crashing Cage on every
  boot with `Failed to open device: '/dev/dri/card0': Operation not
  permitted`. The udev rule write it was meant to unblock actually works
  fine under the default confined profile.
- Fix: `swayidle` was started before Cage's Wayland socket existed, so it
  failed instantly every run ("Unable to connect to the compositor").
  It now waits for the socket the same way the rotation logic already did
  (both now share one `wait_for_wayland_socket` helper).
- Fix: `udevadm control --reload-rules` / `udevadm trigger` had no timeout
  and were observed hanging ~60s waiting for a reply that never came,
  adding a full minute to every boot. Both are now bounded with `timeout 5`.
- Improved the Supervisor-API-unreachable log message to call out the
  stale-registration-after-slug-change case (see Known Issues) alongside
  the existing transient-race case.

## 2026.08.20.1
- Fix (see revert above): add-on failed to start ("app fails to load")
  because the touch calibration udev rule write to /etc/udev/rules.d/ was
  believed to be denied by the Supervisor's default AppArmor confinement.
  Set `apparmor: false` in config.yaml and made the rule write non-fatal in
  run.sh.

## v1.999 - August 2026

- Initial private release
