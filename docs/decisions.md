# Decisions

Dated entries for fixes that were tried, rejected, or reverted, and why.
See `wayland-kiosk/CHANGELOG.MD` for the complete dated release history;
this file covers only the decisions that still shape how the current code
should be read.

## `full_access: true` replaced with an explicit `devices:` list (2026-08-20.3)

The app originally requested `full_access: true`, granting every device on
the host. Replaced with individual device node paths
(`/dev/dri/card0`/`card1`/`renderD128`/`renderD129`,
`/dev/input/event0`-`event25`) so the app holds only the GPU and input
nodes it actually needs. See `docs/security.md`.

## `apparmor: false` tried and reverted (2026-08-20.1, reverted 2026-08-20.2)

Set once to try to unblock a udev rule write that was believed to be denied
by AppArmor confinement. A field log showed it broke `/dev/dri` device
cgroup access instead -- Supervisor ties cgroup device rules to the
AppArmor profile -- crashing Cage with `Failed to open device:
'/dev/dri/card0': Operation not permitted` on every boot. The real cause of
the original crash was unrelated: the `devices:` list used the legacy
Docker triplet syntax (`/dev/dri:/dev/dri:rwm`), which Supervisor's
udev-backed device resolution silently skips. Fixed for real in 2026.08.20.3
by switching to explicit individual device node paths. `apparmor: false`
was removed and must not be reintroduced.

## Touch calibration moved from a udev rule to a Cage compositor patch (2026-08-20.3)

An earlier version tried to write a udev rule
(`LIBINPUT_CALIBRATION_MATRIX` / `WL_OUTPUT`) into the container and reload
udev to map touch input to the correct output. That cannot work: `udev:
true` bind-mounts Home Assistant OS's host `/run/udev` database read-only,
so a rule written inside the container is invisible to the host's udevd,
and `udevadm control --reload-rules` just hangs trying to reach the host
daemon (the "Terminated timeout 5 udevadm" log line). Replaced with a
patch to Cage itself that maps unassigned input devices to a
runtime-supplied output connector. See `docs/design.md`.

## Tab-restore stacking: JSON edit replaced a sed-based fix (2026-08-23.2, then 2026-08-30.1)

The first fix for Chromium restoring stacked tabs on every restart rewrote
`Default/Preferences` with `sed`. Field testing showed that did not
reliably hold. Replaced with a real JSON parse/edit via `python3`, which is
immune to formatting differences a text substitution can miss, plus
deleting the `Default/Sessions` snapshot files the stacked tabs were
rebuilt from. See `docs/design.md`.

## `--bwsi` (ephemeral profile) removed in favor of a persistent profile (2026.08.20.x era)

Running Chromium with `--bwsi` wiped its profile on every restart, which
meant a logged-in session never persisted and forced a fresh auto-login (or
reliance on `trusted_networks` bypass) after every container restart. The
persistent profile under `/data/chromium-profile` fixes that, at the cost
of the tab-restore-stacking problem described above, which is handled
separately.

## Base image stays on `:latest`, not a pinned Alpine release (2026-09-03)

The `build.yaml` migration (see `wayland-kiosk/build.yaml`'s retirement,
blog/2026-04-02-builder-migration.md) initially pinned `BUILD_FROM` to
`ghcr.io/home-assistant/base:3.23`, matching the pattern the
`home-assistant/apps-example` Dockerfile uses. That broke the image build:
`apk add wlroots0.20-dev` failed with "no such package". Checking
pkgs.alpinelinux.org showed `wlroots0.20-dev` exists only in Alpine edge;
both the 3.22 and 3.23 stable branches package a single unversioned
`wlroots-dev` instead. `ghcr.io/home-assistant/base:latest` tracks Alpine
edge, which is what the previous per-arch `:latest` base images
(`amd64-base:latest`, `aarch64-base:latest`) also tracked. `BUILD_FROM` was
reverted to `:latest` for this reason; it must not be pinned to a specific
Alpine release without first confirming `wlroots0.20-dev` (or whatever
Cage's wlroots dependency becomes) is available on that release.

## aiohttp installed via pip, not the py3-aiohttp apk package; py3-requests dropped (2026-09-03)

Adding the Grype vulnerability scan (see docs/security.md) found
`py3-aiohttp` at 3.13.5 with a High-severity fix published at 3.14.3.
Alpine's packaged version lags upstream releases, so `apk upgrade` cannot
reach the fix. Switched to `pip install aiohttp`, which tracks upstream
releases directly. While auditing that package list, `py3-requests` was
also removed: `rest_server.py` imports `urllib.request` and `aiohttp`, not
`requests`, so the package was dead weight with no functional use.

## `register_function`'s required/validators were unenforced

`rest_server.py`'s `register_function` decorator originally accepted
`required` and `validators` arguments but did nothing with them, so a
request missing a required field raised a raw `KeyError` inside the
handler, which surfaced to the caller as an opaque 500. The decorator now
checks `required` fields and runs `validators` before the handler executes,
returning a structured `{"success": false, "error": ...}` response instead.

## `wlr-randr --output *` is not valid

`wlr-randr` has no wildcard support; `--output *` fails with "unknown
output *". This affected screen-timeout blanking and the REST
`display_on`/`display_off` endpoints identically, both fixed by always
passing the real discovered connector name. See `docs/operations.md`.

## Script-level fallback defaults corrected to match `config.yaml` (2026.08.24.1)

`run.sh`'s hardcoded fallback values for `rotate_display` ("normal") and
`screen_timeout` (600) did not match `config.yaml`'s actual documented
defaults ("right" and 0). During a Supervisor API outage the script used to
silently apply the wrong default; the fallbacks were changed to match.
This class of bug motivated reading `/data/options.json` directly instead
of depending on the Supervisor API at all (2026.09.01.1), see
`docs/design.md`.

## Touchscreen zoom blocked with `--disable-pinch`, not a page-level workaround (2026-09-06)

A visitor could pinch the touchscreen and zoom the whole dashboard. Home
Assistant's page already forbids user scaling in its viewport meta tag, so
the zoom was not page-level: desktop Chromium implements touchscreen pinch
in the compositor and ignores the meta tag there. Rejected: injecting CSS or
JavaScript into the dashboard through the DevTools port to swallow touch
events, because it would race the periodic refresh and depend on Home
Assistant's markup. Rejected: `--force-device-scale-factor`, which sets a
scale but does not stop the gesture. Chosen: Chromium's own
`--disable-pinch` switch, whose behavior Chromium's kiosk browser test
asserts, plus clearing `partition.per_host_zoom_levels` at startup so a zoom
level already stored in the profile is not restored.

## Time zone applied to /etc/localtime at startup, not left to TZ alone (2026-09-06)

The Supervisor already passes Home Assistant's time zone as `TZ`, so in
theory nothing was needed. The clock still showed UTC on a real kiosk and
the cause could not be observed remotely, so the app now makes all three of
Chromium's zone lookups agree and logs what it applied. Rejected: adding a
`time_zone` option to config.yaml, because a second source of truth would
drift from Home Assistant's setting, which the Supervisor already forwards.
Rejected: forcing the kiosk user's profile to the server zone from the app,
because the app has no supported way to write another user's frontend
settings; the profile choice is documented instead.

## Dialog surface pinned in display-kiosk.yaml (2026-09-08)

More-info popups rendered transparent over the dashboard. The 2026.8+
frontend paints the `ha-dialog` panel with `ha-dialog-surface-background`
and falls back to `card-background-color`, so any theme that makes cards
transparent makes dialogs transparent as well. The theme now sets
`ha-dialog-surface-background` explicitly. The scrim is controlled by
`ha-dialog-scrim-backdrop-filter` (the frontend sets `brightness(68%)` on
`html`, which shadows the older `dialog-backdrop-filter`) and
`mdc-dialog-scrim-color`; the Polymer `iron-overlay-*` keys and
`ha-dialog-background`, `dialog-background-color`, `mdc-dialog-box-shadow`
are not read by the current dialog and were removed. `card-mod-theme` now
names this theme so card-mod theme styles match the active theme. Verified
live against frontend 20260826.6 by reading the dialog panel's computed
background under each theme. The dialog uses the theme selected in the
browser profile, not a view-level `theme:`, so the kiosk profile must
select display-kiosk.

## Light mode block added to display-kiosk.yaml (2026-09-08)

The theme declared only `modes: dark`. In a light-scheme browser Home
Assistant then applied the black top-level backgrounds with its default
light-mode text and card colors, which produced dark text on black and
white card pills. A `light` block now mirrors the `display` theme's light
palette (F5F5F5 surfaces, white cards and dialog surface, 212121 text)
with light-safe minimalist RGB values, so both schemes are readable. The
kiosk itself should still run dark; the light block exists for desktop
and phone profiles that follow the system scheme.

## Navigation lockdown via Chromium managed policy, not a runtime URL watcher (2026-09-09)

The kiosk could be browsed away from the dashboard by following links out of
Home Assistant. Rejected: policing the current URL from `rest_server.py`'s
DevTools connection and navigating back, as the only mechanism -- it is a
race by construction (the off-host page is loaded and rendered before
anything notices) and it depends on the CDP connection being healthy, which
is precisely what the watchdog exists to doubt. Rejected:
`--host-resolver-rules` to make every other host fail DNS, which also breaks
subresources the dashboard legitimately loads from other hosts, the map
card's tiles being the obvious one. Chosen: a Chromium managed policy
(`URLBlocklist` `*` plus one `URLAllowlist` origin) written before the
browser starts, so the browser refuses the navigation itself, plus
`--block-new-web-contents` so a `target="_blank"` link cannot open a window
that has no close button. `DeveloperToolsAvailability` was left unset on
purpose: this app drives Chromium over the DevTools protocol for the
watchdog, screenshots and refresh, and that policy has been reported to take
the remote endpoint down with the DevTools window -- the risk of losing the
watchdog outweighs a DevTools shortcut that `--kiosk` already suppresses and
that needs a keyboard the panel does not have.

The policy cannot cover in-frontend routing (Settings, Developer tools),
which never issues a filterable navigation. `return_to_dashboard` polls and
navigates home for that, and the docs say plainly that it is a tidy-up and
that a non-admin Home Assistant user is the enforced fix. `KIOSK_URL` and
the lock flag are exported before `rest_server.py` is forked, not with the
Chromium exports at the bottom of `run.sh`, because the server inherits the
environment as it stands when it is started.

## Error-page recovery keyed on `document.documentURI`, not on liveness (2026-09-09)

The kiosk could sit on "This site can't be reached" until a human reloaded
it. The existing watchdog could not see it: it probed whether the renderer
answered, and an error page answers immediately. Rejected: polling the
target list from `/json` and comparing URLs -- verified against Chromium 141
that a browser error page keeps advertising the *original* URL there, so the
target list cannot distinguish it from a healthy dashboard. Rejected:
checking for a Home Assistant DOM root such as `home-assistant`, which would
false-positive on the login page (`ha-authorize`) and reload it in a loop.
Rejected: subscribing to CDP events over a persistent websocket, which would
fight the serialized one-shot `CDP_LOCK` design the rest of the server is
built on. Chosen: evaluate `document.documentURI` as the watchdog's existing
liveness probe -- same cost as evaluating `1` -- and treat a
`chrome-error://` prefix as a reload trigger. It reuses one CDP round trip
for both questions and needs no new connection style.

Reloading a crashed renderer moved from the second failed probe to the
first, bounded to once per episode. The original delay existed to avoid
restarting a busy renderer, but the cost it was avoiding is a page reload,
while the cost it was imposing is a minute or more of dead panel. The
once-per-episode bound keeps the original protection where it mattered: a
slow dashboard is not reloaded on every cycle.

## `dark_mode` implemented as `--force-dark-mode`, without Blink auto-darkening (2026-09-09)

The option had been accepted by the schema and read nowhere. Rejected:
`--enable-features=WebContentsForceDark` (Blink's auto-dark filter), which
inverts the page's own colors -- on a frontend that already ships a dark
theme that means inverted images and a second darkening pass over dark
surfaces. Rejected: forcing a theme on the Home Assistant side from the app,
for the same reason the time-zone decision rejects it -- the app has no
supported way to write another user's frontend settings. Chosen:
`--force-dark-mode`, which was verified against Chromium 141 to flip
`prefers-color-scheme` to dark for the page while leaving the page's own
colors alone. The limit is documented rather than worked around: a Home
Assistant user whose profile pins a light theme stays light, because that is
an explicit choice the frontend honors over the browser's preference.
