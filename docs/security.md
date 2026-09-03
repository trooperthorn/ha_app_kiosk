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

## AppArmor stays on

`config.yaml` does not set `apparmor: false`, and should not. That was
tried once against an earlier `/dev/dri` "Operation not permitted" crash;
it did not fix the crash (the real cause was malformed `devices:` entries,
see `docs/decisions.md`) and only removed the app's security confinement
with no offsetting benefit.
