# HAOS Wayland Kiosk
- VT Binding update
- Chrome Loaded
- Touch moved this to full access
- Rotation and touch rework
- fallback on my cheap touch if dynamic fails


## A streamlined, hardware-accelerated kiosk integration for Home Assistant Operating System.

By replacing the aging X11 stack with a direct Wayland compositor (Cage), this add-on provides a robust, tear-free environment for rendering Chromium dashboards on your local hardware.

## Security update: required setup before installing

This release removes host networking and broad input-device grants. Review
these options before starting: select the touchscreen/pointer devices, check
the HA URL, and update any control-API automations. Reauthenticate the kiosk if
the URL origin changed. Keep Protection mode enabled. A full non-root compositor
and AppArmor hardware check on your HAOS installation is required after update.

| Option | Default | Behavior |
|---|---|---|
| `allow_http` | true | Supports ordinary local HTTP. Disable to require HTTPS navigation. |
| `allow_self_signed` | false | Trust only the certificate supplied below; never globally bypass TLS validation. |
| `self_signed_certificate` | empty | One public PEM server/CA certificate. Hostname and expiry checks still apply. |
| `audio_enabled` | true | Disable to mute playback and remove the browser's PulseAudio connection. Restart required. |
| `control_api_enabled` | false | Enable only with a 32+ character API token and updated internal-network automation URL. |
| `gpu_devices` | card0, renderD128 | Explicit GPU/device selection. |
| `input_devices` | empty | Select this display's touch and pointer devices. |

HTTP needs no certificate. For self-signed HTTPS, copy the server's public PEM
certificate (or your private CA's public certificate) into the option, enable
trust, save and restart. Use the server name listed in the certificate. Never
paste a private key. Disabling trust removes the imported certificate on restart.
Audio uses the Supervisor audio mount so it can be enabled; the toggle controls
browser playback/connection, not Supervisor's static mount permission.

## Configuration Options

Honesty note: not every option in the configuration screen is applied yet.
The table below states exactly which options are wired into run.sh and which
are accepted by the schema but currently have no effect. Do not assume an
option works because it appears in the UI.

| Option | Status | Effect |
|---|---|---|
| `ha_url` | applied | URL the kiosk loads. Default `http://homeassistant:8123` on the private app network. |
| `ha_dashboard` | applied | Appended to `ha_url` as a path. Default `lovelace`, which is Home Assistant's Overview dashboard. |
| `rotate_display` | applied | Output transform via `wlr-randr`: `normal`, `right` (270), `inverted` (180), `left` (90). Rotation is applied before Chromium launches. Unassigned touch devices are mapped to the discovered output by the bundled Cage patch. |
| `screen_timeout` | applied | Seconds of idle before the output powers off via swayidle. `0` disables blanking (default). Fallback also `0`. |
| `auth_method` | applied | `none` (default, persistent manual login), `credentials` (verified-origin form submission), or `trusted_networks` (requires matching Core settings). |
| `ha_username` / `ha_password` | applied | Only used when `auth_method: credentials`. |
| `login_delay` | applied | Seconds to wait for the login page before giving up on auto-login. |
| `ignore_certificate_errors` | retired | Ignored legacy option; use explicit certificate trust below. |
| `api_token` | applied | Required when the control API is enabled; minimum 32 characters. |
| `browser_refresh` | applied | Periodic page refresh interval in seconds. `0` disables periodic refresh. |
| `lock_navigation` | applied | `false` by default. When `true`, a Chromium managed policy blocks every URL except the scheme, host and port of `ha_url`, and new windows are refused outright. See "Locking the kiosk to the dashboard" below. |
| `return_to_dashboard` | applied | Seconds between checks that the page is still on the dashboard; if it is not, the browser is navigated back to `ha_url` + `ha_dashboard`. `0` (default) disables the check. |
| `ha_sidebar` | not applied yet | Accepted by the schema, read nowhere. Hiding the sidebar is better done with the kiosk-mode frontend plugin inside Home Assistant. |
| `ha_theme` | not applied yet | Accepted by the schema, read nowhere. Set the theme per-user in Home Assistant instead. |
| `dark_mode` | applied | `true` by default. Starts Chromium with `--force-dark-mode`, so the browser reports `prefers-color-scheme: dark`. Home Assistant follows that only when the kiosk user's theme is set to Auto; see "Always-dark browser" below. |

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

## Always-dark browser

`dark_mode: true` (the default) starts Chromium with `--force-dark-mode`.
That makes the browser report `prefers-color-scheme: dark` to the page,
which is the signal Home Assistant's own theme handling reads.

The browser flag decides what the page is *told*, not what Home Assistant
*does* with it. For the dashboard to actually come up dark, the kiosk's
Home Assistant user must have its theme set to follow the browser: profile,
Themes, with the mode selector set to **Auto**. A profile pinned to a light
theme stays light no matter what the browser reports, because at that point
the user has made an explicit choice and the frontend honors it. Pinning
the profile to a dark theme (this repository ships `display-kiosk`) is the
other way to get a guaranteed-dark panel, and it does not depend on this
option at all.

What this option deliberately does not do is force-darken the page. Chromium
can invert a page's colors for you (`--enable-features=WebContentsForceDark`),
and on a frontend that already has a real dark theme the result is worse than
either theme on its own -- inverted images, washed-out cards, and a second
darkening pass over already-dark surfaces. See docs/decisions.md.

## The kiosk sits on an error page after Home Assistant restarts

If the panel needs a manual reload "every so often", this is the first thing
to check, and it is not a resource problem.

When Home Assistant Core restarts -- an update, a YAML reload, a Supervisor
operation -- the kiosk's page can fail to load and Chromium replaces it with
its own "This site can't be reached" error page. Before 2026.09.09.x nothing
recovered that state: the watchdog only ever asked whether the renderer was
*responsive*, and an error page is a perfectly healthy renderer that answers
instantly. The page then sat there until someone reloaded it by hand or
`browser_refresh` came around, which at its default of 12000 seconds is over
three hours.

The watchdog now reads the document URI on every 30-second cycle. Chromium
error pages report a `chrome-error://` URI, so the kiosk reloads within
about 30 seconds of Core coming back, and keeps retrying every 30 seconds
while it is still down. The log says so, once per five minutes while it
persists:

```
Watchdog: the kiosk is showing a browser error page (Home Assistant
unreachable when the page loaded); reloading. Retrying every 30s until it
comes back.
```

A crashed renderer -- the "Aw, Snap" case -- is the other half. That one does
fail the responsiveness probe, so it was always caught, but the reload used
to wait for the second failed probe and so left a dead screen for a minute
or more. The reload now happens on the first failed probe (roughly 15-45
seconds), once per episode so a merely slow dashboard is not put into a
reload loop, with the escalation to a container restart unchanged behind it.

## Is it a resource problem?

The app logs one memory line every 30 minutes, so the question can be
answered from the add-on log instead of guessed at:

```
Memory: container 812 MiB (21% of 3814 MiB), Chromium 640 MiB across 7
process(es), peak 690 MiB.
```

Read it as a trend across a day, not as a single number. Chromium is
multi-process and RSS counts shared pages once per process, so the Chromium
figure over-reports; what matters is whether it climbs steadily between
restarts. A flat line that sits well under the limit means memory is not the
problem and the error-page case above is the likelier answer. A line that
climbs until the panel dies is a leak, and the practical mitigation is a
shorter `browser_refresh` -- 1800 (30 minutes) is a reasonable starting
point for a dashboard with camera or map cards, which are the usual cause.

`/api/health` reports the same counters live, including `on_error_page`,
`error_page_reloads` and `unresponsive_reloads`. If `error_page_reloads`
climbs while `unresponsive_reloads` stays at zero, the panel is losing Home
Assistant, not running out of memory.

## Login on the private app network

The default `ha_url` is `http://homeassistant:8123` and `auth_method` is `none`.
Log into the physical kiosk once as a dedicated non-admin HA user; its browser
profile persists. Legacy HTTP loopback URLs are translated to the internal
Home Assistant hostname. HTTPS loopback URLs must be replaced with the hostname
on the certificate. The origin change can require a fresh browser login.

Old Core trusted-network rules for 127.0.0.1 no longer apply. If retaining
`trusted_networks`, configure only this app's verified address and a single
non-admin trusted user. Do not trust the entire Supervisor subnet. Credential
mode remains optional and checks the exact HA origin and login form before
submission; it never types credentials into global keyboard focus. It fails
closed when the expected form is unavailable.

## Time and time zone

The dashboard clock follows the Home Assistant user the kiosk is logged in
as. Every user has a profile setting, Time zone, with two values: "Use your
browser's time zone" (the default for a new user) and "Use server time zone".
With the default, the clock shows whatever the kiosk's own browser believes,
which is the container's time zone.

The app's **Time zone** option defaults to US Central Time. Set it to
`America/Chicago`, save, and restart the app. This selects CDT (UTC-5) in
summer and CST (UTC-6) in winter automatically. Do not use a fixed UTC offset.
The option is independent of a profile opened on another computer.

Existing installations should check this option after updating and restart the app.
Leave the option empty to inherit Supervisor's `TZ`, then `/etc/timezone`,
then the `/etc/localtime` symlink or matching installed timezone rules.
An explicit option takes precedence, including when inherited `TZ` is UTC.
Invalid explicit names stop startup with an error instead of silently using UTC.
The app exports the resolved name to Chromium without replacing mounted files.

Check both startup messages: `Time zone resolved as America/Chicago` records
the requested zone; `Chromium time zone:` records the browser's actual `Intl`
zone and local time. If these show Central but a card still shows UTC, inspect
that card's timezone setting and the profile inside the kiosk browser. Profile
preferences or custom cards can select a zone independently of the browser.

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

## Selected device access

Select `gpu_devices` (normally card0 and renderD128) and `input_devices`
(the touchscreen's touch and pointer event nodes) in Configuration. The input
list defaults empty rather than granting every keyboard/input event. Use
Settings > System > Hardware to identify devices; stable by-id paths are
preferred where available. Save and restart after selection, then verify touch,
rotation and camera decoding. Supervisor derives access from these device
options. No full_access, extra host capability, raw USB or host D-Bus is needed.

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

## REST control API

Enable `control_api_enabled` and set a random `api_token` of at least 32
non-whitespace characters to use automations. The default is disabled; the
watchdog and screen timeout still operate. No port is published on the host.
Core uses `http://<app-hostname>:8034/api`, where the hostname is the installed
app slug with underscores replaced by hyphens. Read the actual slug from the
app information; do not assume a repository hash.

Requests must be server-side POSTs with `Content-Type: application/json` and
`Authorization: Bearer <api_token>`. Browser Origin headers are rejected.
Commands retain their existing names (display_on, display_off, refresh_browser,
launch_url, wlr_randr, is_display_on, screenshot). Update automations previously
using 127.0.0.1:8034. A token authenticates this app API only, not HA itself.
`GET /api/health` returns status on the internal app network when the API is on.
Chromium debugging remains on private container loopback and is not published.
