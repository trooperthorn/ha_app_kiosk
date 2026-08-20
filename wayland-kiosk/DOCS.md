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

## Why `apparmor: false` is set in config.yaml

Touch-rotation calibration works by writing a udev rule to
`/etc/udev/rules.d/99-touch-kiosk.rules` and then calling `udevadm control
--reload-rules` / `udevadm trigger`. The Supervisor's default AppArmor
profile (applied automatically to any add-on that doesn't ship its own
`apparmor.txt`) does not permit writes under `/etc/udev/rules.d/`, so that
step fails with Permission denied. Because `run.sh` runs under bashio's
`errexit`, an unguarded failure there used to kill the whole script before
Cage/Chromium ever started -- the add-on would simply fail to load. `udev:
true` alone does not fix this: it grants access to the device nodes and udev
*events*, not to writing rule files.

`apparmor: false` disables that confinement for this add-on so the rule
write and udevadm calls succeed. `run.sh` also treats a failed rule write as
non-fatal (logs an error and continues) so the kiosk display still starts
even if this write fails for some other reason -- only touch calibration is
lost, not the whole app.

## REST API Server

This add-on spins up a background API server allowing you to control the screen state dynamically from Home Assistant automations:
* `display_on`: Wakes up the monitor.
* `display_off`: Powers down the monitor output via `wlr-randr`.
* `refresh_browser`: Triggers an active reload of the Chromium dashboard.
* `launch_url`: Uses the Chrome DevTools Protocol to seamlessly navigate to a new page.
