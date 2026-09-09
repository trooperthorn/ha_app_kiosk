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
| `rotate_display` | applied | Output transform via `wlr-randr`: `normal`, `right` (270), `inverted` (180), `left` (90). Rotation is applied before Chromium launches. Unassigned touch devices are mapped to the discovered output by the bundled Cage patch. |
| `screen_timeout` | applied | Seconds of idle before the output powers off via swayidle. `0` disables blanking (default). Fallback also `0`. |
| `auth_method` | applied | `trusted_networks` (default, needs Core-side configuration, see below), `credentials` (types the login form once via wtype), or `none`. |
| `ha_username` / `ha_password` | applied | Only used when `auth_method: credentials`. |
| `login_delay` | applied | Seconds to wait for the login page before giving up on auto-login. |
| `ignore_certificate_errors` | applied | Passes `--ignore-certificate-errors` to Chromium for self-signed HTTPS. |
| `api_token` | applied | Optional app-local bearer token for the kiosk control API on `127.0.0.1:8034`. This is **not** a Home Assistant long-lived access token. |
| `browser_refresh` | applied | Periodic page refresh interval in seconds. `0` disables periodic refresh. |
| `lock_navigation` | applied | `false` by default. When `true`, a Chromium managed policy blocks every URL except the scheme, host and port of `ha_url`, and new windows are refused outright. See "Locking the kiosk to the dashboard" below. |
| `return_to_dashboard` | applied | Seconds between checks that the page is still on the dashboard; if it is not, the browser is navigated back to `ha_url` + `ha_dashboard`. `0` (default) disables the check. |
| `ha_sidebar` | not applied yet | Accepted by the schema, read nowhere. Hiding the sidebar is better done with the kiosk-mode frontend plugin inside Home Assistant. |
| `ha_theme` | not applied yet | Accepted by the schema, read nowhere. Set the theme per-user in Home Assistant instead. |
| `dark_mode` | not applied yet | Accepted by the schema, read nowhere. Set dark mode per-user in Home Assistant instead. |

## Locking the kiosk to the dashboard

A wall panel that can reach the open Internet is a wall panel someone will
browse on. Two options close that off, and they cover different halves of the
problem -- use both.

`lock_navigation: true` keeps the browser on one host. At startup the app
writes a Chromium managed policy (`URLBlocklist: ["*"]` plus a single
`URLAllowlist` entry for the scheme, host and port of `ha_url`) into
`/etc/chromium/policies/managed/kiosk-lockdown.json`. With the default
`ha_url`, only `http://127.0.0.1:8123` loads; a link to
`home-assistant.io`, a `file://` URL, or a `chrome://` page is refused by
Chromium itself with a "Blocked Page" notice, and there is no address bar to
type into. The same policy turns off popups, downloads, printing, incognito,
the password manager, autofill, browser sign-in and sync, and Chromium is
started with `--block-new-web-contents` so a `target="_blank"` link cannot
open a second, unclosable window. The add-on log line to look for is
"Navigation lockdown ON". Turning the option back off removes the policy file
on the next start.

What this does **not** do is keep the user on the dashboard *inside* Home
Assistant. Home Assistant's frontend is a single-page app: moving from the
dashboard to Settings or Developer tools never issues a URL request the
browser can filter, so no browser policy can see it, let alone block it.

Two things cover that half:

1. **Log the kiosk in as a non-admin Home Assistant user.** This is the real
   fix, and the only one that is actually enforced -- a non-admin user has no
   Settings or Developer tools to reach. Give that user only the dashboard it
   should see, and use it for the kiosk's `trusted_users` entry or its
   `ha_username`.
2. **`return_to_dashboard: 60`** (any number of seconds) makes the app poll
   the current page and navigate back to the dashboard when it has wandered
   off. Sub-views such as `/lovelace/kitchen` count as being on the
   dashboard, so a panel with several views is not yanked back to the first
   one; `/config/...` and `/developer-tools/...` are not, so a stray tap
   unwinds itself within the interval. The login flow (`/auth/...`) is left
   alone so it cannot be caught in a redirect loop. This is a tidy-up, not a
   restriction: for the seconds before the next check, the page is wherever
   the user put it.

Scope, plainly: this is protection against a curious visitor with a
touchscreen. Someone with a USB keyboard, physical access to the machine, or
the ability to change add-on options is a different threat model and this
option does not address it.

Note for the control API: while `lock_navigation` is on, the `launch_url`
command only accepts URLs on the same origin as `ha_url` and returns an
error for anything else, rather than letting Chromium silently show a
blocked page. `/api/health` reports the current state as
`navigation_locked`.

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

Do not list `127.0.0.1` or `::1` as an HTTP `trusted_proxy` for this
configuration. Home Assistant excludes trusted-proxy addresses from the
trusted-networks authentication provider. If the Network settings UI
requires at least one proxy entry even though `use_x_forwarded_for` is off,
use an unused documentation address such as `192.0.2.1/32` instead.

## Time and time zone

The dashboard clock follows the Home Assistant user the kiosk is logged in
as. Every user has a profile setting, Time zone, with two values: "Use your
browser's time zone" (the default for a new user) and "Use server time zone".
With the default, the clock shows whatever the kiosk's own browser believes,
which is the container's time zone.

The app inherits Home Assistant's configured time zone automatically: the
Supervisor passes it to the container, and on startup the app applies it to
every place Chromium looks (the `TZ` variable, `/etc/localtime`, and
`/etc/timezone`), then logs the zone it applied. So with either profile
setting the clock should match Settings, System, General, Time zone.

If the clock still shows UTC, set the kiosk user's profile to "Use server
time zone": it makes the browser irrelevant and is the right choice for a
display that sits in the same house the server is configured for. Then check
the app log for the "Time zone from Home Assistant" line; a warning there
means the zone name had no matching zoneinfo file, which is worth reporting.

## Boot and reboot behavior

The add-on is designed to survive a host reboot without intervention:

1. Enable the **Start on boot** toggle on the add-on's Info page. Supervisor
   then starts the add-on at every boot (`startup: application` orders it
   after Home Assistant Core is launched).
2. Enable the **Watchdog** toggle as well. rest_server.py monitors Chromium
   through the DevTools endpoint. A failed renderer check gets a page-reload
   recovery attempt, and Cage is terminated only after three consecutive
   15-second failures plus a final 30-second confirmation. The container then
   exits and Supervisor's watchdog restarts it. Without the toggle, a genuinely
   frozen kiosk stays stopped after that recovery path is exhausted.
3. At a cold boot, Core is often launched but not yet serving HTTP when this
   add-on starts. run.sh waits (up to 5 minutes) for an HTTP response from
   `ha_url` before starting the browser, because Chromium never retries a
   failed connection on its own. The screen stays black during that wait
   instead of parking on a dead "connection refused" page.
4. Add-on options are read directly from `/data/options.json`; kiosk startup
   no longer depends on the Supervisor HTTP API or waits through a 403 retry
   loop.
5. Cage starts a small launcher first. It rotates the output before creating
   Chromium's surface, then starts Chromium with `--app`, `--kiosk`, and
   `--start-fullscreen`. App mode removes tabs and the address bar even if a
   Wayland fullscreen request is left in the maximized state.

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

On AMD systems, the image includes Mesa's Gallium VA-API backend so Chromium
can decode supported camera video formats on the GPU. At startup the add-on
tests `/dev/dri/renderD128` with `vainfo`. A healthy AMD host logs
`VA-API hardware video decoding available` followed by the Mesa driver
name. A probe failure is non-fatal, but means video decoding will use CPU
until the DRM permissions or driver are corrected.

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

## Touchscreen mapping and rotation

wlroots applies an output transform to touch coordinates only after the
touch device is mapped to that output. USB panels such as the ILITEK device
often provide no `WL_OUTPUT` udev property, causing stock Cage to log
`cannot be mapped to an output device` and leave the coordinates
untransformed.

The add-on builds Cage with a narrow patch: run.sh discovers the connected
DRM connector and exports it as `CAGE_TOUCH_OUTPUT`; when that connector is
initialized, Cage maps touch and absolute-pointer interfaces which lack an
output assignment to it. Explicit device-to-output assignments are still
honored, and the automatic fallback is limited to the selected connector.

This is done in the compositor because `udev: true` bind-mounts Home
Assistant OS's `/run/udev` database read-only. Rules written inside the
container are invisible to the host's udev daemon, and attempting to reload
them from the add-on can hang.

## If `rotate_display` (or any other option) never takes effect

The startup log should contain `Loaded add-on configuration from
/data/options.json` followed by the final kiosk URL and rotation transform.
If the file is missing or invalid, the add-on logs a warning and uses the
documented defaults. Supervisor API/token health no longer affects option
loading.

## REST API Server

This add-on spins up a background API server allowing you to control the screen state dynamically from Home Assistant automations:
* `display_on`: Wakes up the monitor.
* `display_off`: Powers down the monitor output via `wlr-randr`.
* `refresh_browser`: Triggers an active reload of the Chromium dashboard.
* `launch_url`: Uses the Chrome DevTools Protocol to seamlessly navigate to a new page.
* `screenshot`: Returns a base64-encoded PNG of whatever is currently on screen.

The `api_token` option is a shared secret belonging only to this kiosk app.
Do not paste a Home Assistant long-lived access token into it. When the option
is set, every control request must contain:

```text
Authorization: Bearer <your kiosk control API token>
```

Because the API listens only on loopback, it cannot be reached directly from
the LAN. With `host_network: true`, Home Assistant Core and other host-local
processes can reach it.

### Monitoring endpoint

`GET http://<host>:8034/api/health` (no token required) reports app uptime,
whether the monitor is currently on, whether the dashboard is paused for
power saving, and whether Chromium is responding, as plain JSON. Point an
uptime check or a Home Assistant `rest` sensor at it without handling the
control API's token.

### Power saving while the screen is off

Whenever the monitor goes dark, however that happened -- the configured
`screen_timeout`, a `display_off` call, or a `timeout` set through
`display_on` -- the app also pauses the Chromium page's rendering and
timers until the screen comes back on. This is transparent: the dashboard
resumes live the moment the display wakes.
