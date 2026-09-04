# Operations

## Device access mechanics

Supervisor grants an app hardware access by resolving every entry in
`config.yaml`'s `devices:` list against its udev hardware database and
writing a cgroup v2 device rule for each match (`docker/app.py
cgroups_rules` in the Supervisor source). Two consequences follow:

1. Entries must be individual device nodes (`/dev/dri/card0`,
   `/dev/input/event3`, ...). A directory (`/dev/dri`) or the legacy Docker
   triplet form (`/dev/dri:/dev/dri:rwm`) matches nothing in the hardware
   database and is silently skipped at debug-log level: the app installs
   and starts normally but holds zero device grants. The nodes stay visible
   in `/dev` inside the container, but every `open()` fails with `EPERM`,
   which surfaces as wlroots' `Failed to open device: '/dev/dri/card0'` ->
   `Found 0 GPUs` -> Cage exits. This bug shipped in every version before
   2026.08.20.3.
2. Nodes listed in `config.yaml` that do not exist on a given host are
   skipped harmlessly, so the config deliberately over-lists device nodes
   (`card0`/`card1`, `event0`-`event25`) to cover different hardware.

## The BusyBox `stat` / chromium-launcher.sh quirk

On this Alpine image, both `chromium` and `chromium-browser` on `PATH` are
symlinks to the same `chromium-launcher.sh` wrapper (confirmed in Alpine's
aports `APKBUILD`). That wrapper's root-user safety check
(`[ $(id -u) -eq 0 ] && [ $(stat -c %u -L "$HOME") -eq 0 ]`) calls `stat -L`,
a flag this image's BusyBox `stat` applet does not support (its own usage
text lists only `[-ltf] [-c FMT]`). The broken `stat` call dumps BusyBox's
full usage text into the log on every boot, immediately followed by a
"sh: 0: unknown operand" from the now-empty `$(...)` feeding the `-eq 0`
test. Both lines are harmless: the wrapper's own `exec "$PROGDIR/chromium"`
still runs Chromium regardless. `launch-browser.sh` avoids the noise
entirely by invoking the real binary at `/usr/lib/chromium/chromium`
directly (the exact path the wrapper itself resolves to and execs), with a
runtime fallback to the `chromium-browser` wrapper if that path is ever
missing after a package layout change.

`HOME` is exported as `/root` in `run.sh` for two reasons: Chromium itself
expects `HOME` for its NSS certificate store and dconf lookups when running
as root, and the wrapper's own check above reads `${XDG_CONFIG_HOME:-${HOME}}`
-- with `HOME` unset that check runs with no file argument at all, which is
what actually triggers the BusyBox usage-text dump described above, even on
the fallback path.

## bashio's `set -o pipefail` and short pipelines

bashio runs every script with `set -o errexit -o pipefail` plus
`shopt -s inherit_errexit`. Under that combination, a pipeline like
`grep ... | head -n 1` is a landmine: `head` reads its one line and exits,
and if the upstream `grep` is still writing when that happens, `grep` gets
`SIGPIPE`, which `pipefail` treats as the pipeline's exit status, which
`errexit` then treats as a fatal script error (this is exactly what
produces a container exit code of 141 = 128+SIGPIPE). Whether it triggers
depends on output size versus the pipe buffer, which is why it can pass
testing and then fail intermittently in the field. The touch-device
discovery pipeline in `run.sh` ends with `|| true` for this reason: it does
not change what gets captured, since the text is already on stdout
regardless, but it stops the harmless early pipe close from being treated
as fatal.

## Rotation

`rotate_display` maps to a wlroots output transform:

| `rotate_display` | Transform |
| --- | --- |
| `normal` | `normal` |
| `right` | `270` |
| `inverted` | `180` |
| `left` | `90` |

The transform is applied by `launch-browser.sh` via `wlr-randr` before
Chromium creates its first surface, so Chromium starts at its final
portrait or landscape size instead of being resized after launch.

`wlr-randr` has no wildcard support: `--output *` fails with
"unknown output *". Both the screen-timeout `swayidle` command and the
REST API's `display_on`/`display_off`/`is_display_on` endpoints must target
the real connector name (`KIOSK_OUTPUT` / `ACTIVE_OUTPUT`, auto-discovered
from `/sys/class/drm/*/status` with a static fallback of `DP-1`).

## Background tasks wait for Cage's Wayland socket

Cage only creates its Wayland socket after the compositor starts, so
anything needing `WAYLAND_DISPLAY` -- the screen-timeout `swayidle` job and
the REST API server -- has to wait for it rather than assume it already
exists. `run.sh`'s `wait_for_wayland_socket` helper polls for up to 15
seconds (0.5s interval) instead of using a fixed sleep, since a fixed sleep
can miss a slow compositor start (more likely on aarch64/armv7 hardware),
and each caller logs its own failure context if the socket never appears.

## Startup diagnostics and open-page logging

`run.sh` logs a GPU/DRM device inventory, a VA-API decode probe
(`vainfo --device /dev/dri/renderD128`, non-fatal on failure -- it only
means camera video decodes on CPU instead of the GPU), and a scan of
`/sys/class/drm/*/status` connectors at every boot.

It also samples Chromium's DevTools target list (`http://127.0.0.1:9222/json`)
at roughly 30s and 90s after launch and logs each open page's URL. This
gives a way to diagnose stacked tabs, unexpected redirects, or a stuck
login page from the app log alone, without a photo of the screen. A healthy
boot reports exactly one page.

## Chromium launch flags

`launch-browser.sh` disables Chromium's Google Cloud Messaging phone-home
attempts (`--disable-background-networking`, `--disable-component-update`,
`--disable-features=GCM`), which are useless on a kiosk and otherwise spam
the log with "Registration response error message: DEPRECATED_ENDPOINT /
PHONE_REGISTRATION_ERROR" (see `chromiumembedded/cef#4078` for the same
fix). `--log-level=3` was deliberately not used to silence them instead,
because ERROR-level output has repeatedly been the diagnostic signal for
real bugs in this app. `--disable-dev-shm-usage` works around Docker's
default 64MB `/dev/shm`, which a large dashboard's rendering can exceed,
crashing tabs; this makes Chromium use `/tmp` instead. `--app=<url>` is
used in addition to `--kiosk` because Cage maximizes its single application
window, and `--app` removes tabs and the address bar even on Wayland builds
that ignore Chromium's early fullscreen request.

## `login_delay` truncation

`login_delay` is schema'd as `float(0,)`, so it may arrive as e.g. `"10.5"`.
Bash's `[ -lt ]` only compares integers, so the credentials auto-login loop
in `run.sh` truncates it to whole seconds (`"${LOGIN_DELAY%.*}"`, defaulting
to 10 if that leaves an empty string) before using it as a loop bound.

## REST control API

`rest_server.py` listens on `127.0.0.1:8034` and accepts a single POST
`/api` endpoint with a `command` field selecting one of:

- `display_on` -- turns the output on via `wlr-randr`; an optional
  `timeout` argument (re)configures or disables the `swayidle` blanking
  timer.
- `display_off` -- turns the output off via `wlr-randr`.
- `refresh_browser` -- reloads the current page via CDP `Page.reload`.
- `launch_url` -- navigates to a URL (or the configured `ha_url` if none is
  given) via CDP `Page.navigate`.
- `is_display_on` -- reports whether `wlr-randr` shows the output enabled.
- `wlr_randr` -- runs an arbitrary whitelisted `wlr-randr` invocation with
  caller-supplied arguments (see `docs/security.md` for the whitelist).
- `screenshot` -- captures the current Chromium frame via CDP
  `Page.captureScreenshot` and returns it as a base64-encoded PNG in the
  JSON response (`{"format": "png", "data": "..."}`), rather than as a
  separate binary route, to keep every command behind the same single
  POST endpoint and token check.

A separate unauthenticated `GET /api/health` route reports
`app_uptime_seconds`, `display_on`, `display_frozen`, and
`chromium_responsive` for external monitoring that cannot do a token
exchange; see `docs/security.md` for why this one route skips the check.

See `docs/security.md` for the authentication and network-exposure model.

## Freezing Chromium while the output is blanked

`display_freeze_watcher` in `rest_server.py` polls `wlr-randr` every two
seconds and calls CDP `Page.setWebLifecycleState` to flip the page between
`active` and `frozen` as the output's power state changes. Polling was
chosen over hooking the transition points directly because the output can
go dark through three independent paths that don't share code: run.sh's
static `swayidle` process (driven by the `screen_timeout` option),
a second `swayidle` process started by `display_on`'s `timeout` argument,
and a direct `display_off` or `wlr_randr --off` call. None of those paths
know about `rest_server.py`; polling the one thing they all change
(the output's actual power state) catches all three without needing a
callback into every blanking mechanism.

A frozen page stops rendering and pauses its JS timers, which is what
turns idle-behind-a-blanked-output from full CPU/GPU load into near zero
-- the same effect Kiosk Satellite's screensaver-pause measures on
Android. It also means the page legitimately stops responding to
`chromium_watchdog`'s `Runtime.evaluate` liveness check by design, so the
watchdog checks `RUNTIME_STATE["display_frozen"]` first and skips its
check entirely while frozen, rather than treating an intentionally paused
page as a hang and restarting the container.

## CI gate thresholds

ShellCheck in `security.yml` runs with `--severity=error`. The warning and
info findings it would otherwise report (an unused loop counter, an `ls |
grep` pipeline, a `WAYLAND_DISPLAY` export inside a subshell) are deliberate
or harmless and predate the gate; only a genuine error should block a merge.
`SC1091` is excluded because the scripts source bashio files that are not in
the repository.

hadolint in `validate.yml` runs with `failure-threshold: error`. Its style and
warning findings (an untagged `FROM` resolved from a build argument, apk
packages without pinned versions) are known and not being changed as part of
the release baseline; the base image decision in `decisions.md` explains the
tag choice. The Grype scope for the image scan is described in `security.md`.

