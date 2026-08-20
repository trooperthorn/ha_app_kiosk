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

## Do NOT set `apparmor: false`

This was tried once as a fix for a suspected udev permission issue and made
things worse: Supervisor applies the `devices:` cgroup allow-list for this
add-on together with its AppArmor profile, so disabling AppArmor broke
access to `/dev/dri` entirely -- wlroots failed with `Failed to open
device: '/dev/dri/card0': Operation not permitted`, found 0 GPUs, and Cage
exited immediately, killing the whole container. The default (confined)
profile must stay in place for the explicit `/dev/dri` / `/dev/input`
device passthrough in config.yaml to actually work.

The touch-calibration udev rule write (`/etc/udev/rules.d/99-touch-kiosk.rules`
+ `udevadm control`/`trigger`) runs fine under the default profile. `run.sh`
also guards that write so a permission failure there only costs touch
calibration and never crashes the rest of the add-on.

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

## REST API Server

This add-on spins up a background API server allowing you to control the screen state dynamically from Home Assistant automations:
* `display_on`: Wakes up the monitor.
* `display_off`: Powers down the monitor output via `wlr-randr`.
* `refresh_browser`: Triggers an active reload of the Chromium dashboard.
* `launch_url`: Uses the Chrome DevTools Protocol to seamlessly navigate to a new page.
