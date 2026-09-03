# Security

## `host_network: true`

`config.yaml` sets `host_network: true` so that the configured `ha_url`
(default `http://127.0.0.1:8123`) reaches the real Home Assistant Core
instance. With `host_network` off, `127.0.0.1` inside the container would
resolve to the container's own loopback, not the host's, and the kiosk
could never reach Core over that address. This also means Core's
`trusted_networks` auth provider sees this app's requests as originating
from `127.0.0.1`/`::1` and trusts them by address alone -- see
`wayland-kiosk/DOCS.md` for the `auth_providers` configuration this
requires on the Core side, and the loopback-bind note below for how this
setting also affects the app's own control API.

## REST control API: loopback bind is the actual boundary

`rest_server.py` binds to `127.0.0.1:8034` only, never `0.0.0.0`. This
matters specifically because `config.yaml` sets `host_network: true` (see
below): with host networking, binding to `0.0.0.0` would expose the control
API -- which can redirect the kiosk browser and power the physical display
off -- to the entire local network, not just this container's host. If a
command needs to reach this API from another host, the intended path is a
Home Assistant automation calling it through Supervisor/Core rather than
widening the bind address.

`api_token` (enforced when set) is an app-local shared secret checked
against the `Authorization: Bearer <token>` header on every `/api` request.
It is not, and must not be treated as, a Home Assistant long-lived access
token -- it authenticates callers to this loopback-only API, nothing else.
When unset, the API accepts any request that can reach the loopback
address, which is only this container and, because of `host_network: true`,
other processes on the same host.

## Command whitelist in `rest_server.py`

`execute_command` and `start_background_command` refuse to run anything
whose argv[0] is not in `ALLOWED_COMMANDS = {"wlr-randr", "wtype",
"killall", "swayidle"}`. Every invocation goes through
`asyncio.create_subprocess_exec`, which calls `execve()` directly and never
invokes a shell, so there is no shell-metacharacter injection surface
regardless of caller-supplied arguments; the whitelist is the actual
control, not shell escaping. This applies even to the `wlr_randr` endpoint,
which splits caller-supplied `args` with `shlex.split` and passes the
result straight through to `execve()` as argv -- a caller who can reach the
API can only ever run one of the four whitelisted binaries.

## Device permissions: explicit nodes, not `full_access`

`config.yaml` lists individual device nodes under `devices:`
(`/dev/dri/card0`, `/dev/dri/renderD128`, `/dev/input/event0`-`event25`,
...) instead of `full_access: true`. `full_access: true` grants every
device on the host; this app only ever needs the GPU/DRM nodes and the
input event nodes it discovers at runtime, so the explicit list is the
minimum grant Supervisor's udev-backed device model supports. See
`docs/operations.md` for why the list must be individual nodes rather than
directories, and why over-listing nonexistent nodes is safe.

## Base image package patching

The Dockerfile runs `apk upgrade --no-cache` right after `FROM`, before
installing anything else, so the image ships current Alpine security
patches rather than whatever was cached when `ghcr.io/home-assistant/base`
was last built. This was added 2026-09-03 after the app-image-security
CI job (Grype, `severity-cutoff: high`) found `libssl3`/`libcrypto3`
3.5.7-r0 with fixed High-severity CVEs already published at 3.5.8-r0.

## Image vulnerability scan scope

The app-image-security CI job (Grype) runs with `only-fixed: true` and a
`.grype.yaml` ignore list, added 2026-09-03 after the first run of this
newly added gate. Chromium and ffmpeg between them always carry a large
number of disclosed, currently-unfixed-in-the-packaged-version CVEs; Alpine
edge's `chromium` package alone showed over 150 High/Critical findings with
no `FIXED IN` version at all. Failing CI on every one of those would
permanently block merges rather than catch anything this repository can
act on, so the gate is scoped to findings where a fix is actually
available. `.grype.yaml` additionally ignores a specific set of
golang.org/x/crypto and Go stdlib CVEs that Grype reports as fixed
upstream but that live in Go binaries compiled into the
`ghcr.io/home-assistant/base` image itself, not in anything this
Dockerfile installs -- there is no `apk add` or `pip install` change here
that resolves them. Re-check both scopes whenever `BUILD_FROM`'s effective
Alpine version changes.

## AppArmor stays on

`config.yaml` does not set `apparmor: false`, and should not. That was
tried once against an earlier `/dev/dri` "Operation not permitted" crash;
it did not fix the crash (the real cause was malformed `devices:` entries,
see `docs/decisions.md`) and only removed the app's security confinement
with no offsetting benefit.
