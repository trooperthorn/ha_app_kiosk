# Changelog
## KNOWN ISSUES
- Install slower to add chrome, 1-5 minutes. I can't fix that.
- Locally-installed add-on can end up with a stale Supervisor
  registration/token (constant "Unable to access the API, forbidden") after
  the add-on's slug changes, until it's fully uninstalled and reinstalled.
  Supervisor-side, not fixable from this repo. An orphaned container from a
  previous install (e.g. one named `addon_app_kiosk` without the
  `addon_<repohash>_` / `addon_local_` prefix Supervisor uses today) can
  keep producing "Invalid token" errors and confuse debugging -- check
  `docker ps -a` and `docker rm -f` any kiosk container Supervisor no
  longer manages.

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
