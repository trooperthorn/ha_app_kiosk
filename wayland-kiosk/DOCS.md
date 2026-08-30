# HAOS Wayland Kiosk
- VT Binding update
- Chrome Loaded
- Touch moved this to full access
- Rotation and touch rework
- fallback on my cheap touch if dynamic fails


## A streamlined, hardware-accelerated kiosk integration for Home Assistant Operating System.

By replacing the aging X11 stack with a direct Wayland compositor (Cage), this add-on provides a robust, tear-free environment for rendering Chromium dashboards on your local hardware.

## Configuration Options

Honesty note: not every option in the configuration screen is applied yet.
The table below states exactly which options are wired into run.sh and which
are accepted by the schema but currently have no effect. Do not assume an
option works because it appears in the UI.

| Option | Status | Effect |
|---|---|---|
| `ha_url` | applied | URL the kiosk loads. Default `http://127.0.0.1:8123` (requires `host_network`). |
| `ha_dashboard` | applied | Appended to `ha_url` as a path. Default `lovelace`, which is Home Assistant's Overview dashboard. |
| `rotate_display` | applied | Output transform via `wlr-randr`: `normal`, `right` (270), `inverted` (180), `left` (90). Touch rotates with the output automatically under wlroots. Default `right`; the script fallback matches, so a Supervisor API outage cannot silently un-rotate the display. |
| `screen_timeout` | applied | Seconds of idle before the output powers off via swayidle. `0` disables blanking (default). Fallback also `0`. |
| `auth_method` | applied | `trusted_networks` (default, needs Core-side configuration, see below), `credentials` (types the login form once via wtype), or `none`. |
| `ha_username` / `ha_password` | applied | Only used when `auth_method: credentials`. |
| `login_delay` | applied | Seconds to wait for the login page before giving up on auto-login. |
| `ignore_certificate_errors` | applied | Passes `--ignore-certificate-errors` to Chromium for self-signed HTTPS. |
| `api_token` | applied | Bearer token required by the local REST control API (rest_server.py). Leave blank to run it unauthenticated (not recommended). |
| `browser_refresh` | not applied yet | Accepted by the schema, read nowhere. Intended as a periodic page refresh interval; not implemented. |
| `ha_sidebar` | not applied yet | Accepted by the schema, read nowhere. Hiding the sidebar is better done with the kiosk-mode frontend plugin inside Home Assistant. |
| `ha_theme` | not applied yet | Accepted by the schema, read nowhere. Set the theme per-user in Home Assistant instead. |
| `dark_mode` | not applied yet | Accepted by the schema, read nowhere. Set dark mode per-user in Home Assistant instead. |

## Login: trusted_networks that actually bypasses

For `auth_method: trusted_networks`, Home Assistant Core needs an
`auth_providers` block in its own `configuration.yaml`. **Provider order
matters**: the login flow works through the list in order, so
`trusted_networks` must come FIRST or the kiosk is handed the password
form as the default and never auto-logs-in. Password login everywhere
else keeps working because the `homeassistant` provider stays in the
list as the second entry.

```yaml
homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 127.0.0.1/32
        - ::1/128
      trusted_users:
        127.0.0.1:
          - YOUR_USER_ID_FROM_SETTINGS_PEOPLE_URL
        ::1:
          - YOUR_USER_ID_FROM_SETTINGS_PEOPLE_URL
      allow_bypass_login: true
    - type: homeassistant
```

`allow_bypass_login: true` skips the login screen entirely when exactly
one user is eligible for the connecting IP; `trusted_users` pins which
user that is. After editing, a full Core restart is required
(`auth_providers` is not hot-reloadable), then restart this add-on.
Alternative with no Core changes: log in manually once on the kiosk; the
persistent Chromium profile keeps the session across restarts and
reboots.

## Boot and reboot behavior

The add-on is designed to survive a host reboot without intervention:

1. Enable the **Start on boot** toggle on the add-on's Info page. Supervisor
   then starts the add-on at every boot (`startup: application` orders it
   after Home Assistant Core is launched).
2. Enable the **Watchdog** toggle as well. rest_server.py monitors Chromium
   through the DevTools endpoint and deliberately kills the compositor if
   Chromium freezes; the container then exits and Supervisor's watchdog is
   what restarts it. Without the toggle, a frozen kiosk stays dead.
3. At a cold boot, Core is often launched but not yet serving HTTP when this
   add-on starts. run.sh waits (up to 5 minutes) for an HTTP response from
   `ha_url` before starting the browser, because Chromium never retries a
   failed connection on its own. The screen stays black during that wait
   instead of parking on a dead "connection refused" page.
4. If the Supervisor API rejects the add-on's token during the startup
   window (a recurring Supervisor-side issue, see the troubleshooting
   section), rotation and screen-timeout fall back to the documented
   defaults (`right`, `0`) and a single background poller re-reads the real
   values when the API recovers.

The add-on also logs the browser's actual open pages (the "address bar"
contents) at ~30s and ~90s after launch, so display-side issues can be
diagnosed from the add-on log alone. A healthy boot reports exactly one
page.

## Browser Mod, camera popups, and automations

The kiosk is a normal Home Assistant frontend session, so the
[Browser Mod](https://github.com/thomasloven/hass-browser_mod) integration
(installed via HACS in Home Assistant, not in this add-on) works on it: once
the dashboard is loaded, open the Browser Mod panel from the sidebar on any
other device, find this browser in the list, and register it with a
recognizable Browser ID (for example `kiosk_display`). The add-on's
persistent Chromium profile keeps that Browser ID stable across restarts
and reboots; nothing in the launch flags blocks the websocket Browser Mod
uses.

A camera popup from an automation then looks like this:

```yaml
alias: "Doorbell camera popup on kiosk"
triggers:
  - trigger: state
    entity_id: binary_sensor.doorbell_motion
    to: "on"
actions:
  - action: browser_mod.popup
    data:
      browser_id: kiosk_display
      title: "Front door"
      timeout: 15000
      content:
        type: picture-glance
        camera_image: camera.front_door
        camera_view: live
        entities: []
```

`browser_mod.popup` draws over whatever dashboard the kiosk is showing and
dismisses itself after `timeout` milliseconds. The same mechanism covers
scripts (`action: browser_mod.popup` inside any script) and navigation
(`browser_mod.navigate` to send the kiosk to another dashboard).

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
the *entire* run, every option falls back to its script default. Since
2026.08.24.x those fallbacks mirror the documented defaults (`right`
rotation, screen timeout `0`, `trusted_networks`), so an outage no longer
changes behavior away from the defaults -- but any NON-default values you
configured (a different dashboard, `credentials` auth, a custom URL) still
cannot be read until the API recovers. A background poller retries for
about 5 minutes and applies rotation and screen-timeout late if the API
comes back.

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
