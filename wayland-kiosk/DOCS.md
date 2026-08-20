# HAOS Wayland Kiosk
- VT Binding update
- Chrome Loaded
- Touch moved this to full access
- Rotation and touch rework
- fallback on my cheap touch if dynamic fails


## A streamlined, hardware-accelerated kiosk integration for Home Assistant Operating System.

By replacing the aging X11 stack with a direct Wayland compositor (Cage), this add-on provides a robust, tear-free environment for rendering Chromium dashboards on your local hardware.

## Configuration Options

* **ha_url**: The URL the kiosk should load on boot. (Default: `http://supervisor/core`)
* **rotate_display**: Hardware rotation mapping. Supports `normal`, `right` (90°), `inverted` (180°), or `left` (270°).
* **ignore_certificate_errors**: Set to `true` if your URL relies on self-signed SSL certificates.

## Device access: how it works and how it broke

Supervisor grants an add-on hardware access by resolving every entry in
config.yaml's `devices:` list against its udev hardware database and
writing a cgroup v2 device rule for each match (`docker/app.py`
`cgroups_rules` in the Supervisor source). Two things follow from that:

1. **Entries must be individual device nodes** (`/dev/dri/card0`,
   `/dev/input/event3`, ...). A directory (`/dev/dri`) or the legacy
   Docker triplet form (`/dev/dri:/dev/dri:rwm`) matches nothing in the
   hardware database and is **silently skipped** -- the add-on installs
   and starts normally but holds zero device grants. The device nodes are
   still *visible* in `/dev` inside the container, but every `open()`
   fails with `EPERM` ("Operation not permitted"), which surfaces as
   wlroots' `Failed to open device: '/dev/dri/card0'` -> `Found 0 GPUs`
   -> Cage exits. This exact bug shipped in every version before
   2026.08.20.3.
2. Nodes listed in config.yaml that don't exist on a given host are
   skipped harmlessly, so the config deliberately over-lists
   (`card0`/`card1`, `event0`-`event25`) to cover different hardware --
   the same approach the proven HAOS-kiosk X11 add-on uses.

Do NOT set `apparmor: false`: it was once tried against this same crash,
does not affect device cgroup grants, and only strips the add-on's
security confinement.

## Touch rotation needs no udev calibration

wlroots (Cage's backend) maps touchscreens to the output and applies the
output's transform to touch coordinates, so `wlr-randr --transform`
rotates the display and touch input together. A previous version wrote a
`LIBINPUT_CALIBRATION_MATRIX` udev rule and ran `udevadm control` /
`trigger`; that mechanism cannot work inside an add-on -- `udev: true`
bind-mounts the **host's** `/run/udev` read-only, so container-written
rules are invisible to the host's udevd and `udevadm control` just hangs
against the host daemon's socket.

## If `rotate_display` (or any other option) never takes effect

Check the add-on log for repeated `Unable to access the API, forbidden` /
`Failed to get addon config from Supervisor API` lines. If that only
happens once or twice at boot, it's a known transient Supervisor race
(home-assistant/supervisor#1930) and resolves itself. If it happens for
the *entire* run, every option silently falls back to its script default
(`rotate_display` becomes `normal`, `screen_timeout` becomes `600`, etc.)
regardless of what's configured in the add-on's UI -- this looks like
rotation "not working" but is actually the config never being read at all.

This add-on's slug changed from `haos_wayland_kiosk` to `app_kiosk` on
2026-08-13. If you had it installed under the old slug, Supervisor can be
left serving requests against a stale registration/token after an
in-place update. Fix: fully **uninstall** the add-on, then reinstall it
(not just rebuild/restart) so Supervisor issues a fresh registration for
the current slug.

If the errors survive even a full uninstall/reinstall, look for an
**orphaned container** from an earlier install: Supervisor names managed
containers `addon_local_app_kiosk` (local install) or
`addon_<8-hex-hash>_app_kiosk` (git repository install). Run
`docker ps -a | grep -i kiosk` on the host -- any kiosk container with a
different name (for example a bare `addon_app_kiosk`, or one named for
the old `haos_wayland_kiosk` slug) is a leftover Supervisor no longer
manages. Its baked-in token can never validate again ("Invalid token" in
the Supervisor log) and it may restart itself at boot via Docker's
restart policy. Remove it: `docker rm -f <name>`.

## REST API Server

This add-on spins up a background API server allowing you to control the screen state dynamically from Home Assistant automations:
* `display_on`: Wakes up the monitor.
* `display_off`: Powers down the monitor output via `wlr-randr`.
* `refresh_browser`: Triggers an active reload of the Chromium dashboard.
* `launch_url`: Uses the Chrome DevTools Protocol to seamlessly navigate to a new page.
