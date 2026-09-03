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
