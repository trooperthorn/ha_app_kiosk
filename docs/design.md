# Architecture

## Options are read from `/data/options.json`, not the Supervisor API

`run.sh` reads validated add-on options by parsing `/data/options.json`
directly instead of calling the Supervisor's `/addons/self/options` API.
Supervisor writes that file with the validated configuration before it
starts the container. Earlier versions called the Supervisor API instead,
which returned 403 for up to several minutes after a boot (a token
registration race), silently discarding every non-default option until the
API recovered. Reading the file removes that race entirely; option loading
no longer depends on Supervisor API or token health at all.

## Touch-to-output mapping happens in the compositor, not in udev

Rotating a display also has to rotate touch input, and wlroots only applies
that transform to a touch device once it is mapped to an output. USB touch
panels (including the ILITEK panel this app was built against) commonly
omit the `WL_OUTPUT` udev property, so Cage cannot make that mapping on its
own.

The app carries a small patch to Cage (`cage-map-unassigned-input.patch`,
applied during the Docker build) that maps any input device lacking an
output assignment to a connector supplied at runtime through the
`CAGE_TOUCH_OUTPUT` environment variable. `run.sh` discovers the active DRM
connector at startup and exports it before launching Cage.

An earlier design instead tried to write a udev rule
(`LIBINPUT_CALIBRATION_MATRIX` / `WL_OUTPUT`) into the container and reload
udev. That cannot work here: `udev: true` bind-mounts Home Assistant OS's
host `/run/udev` database read-only into the container (Supervisor
`docker/const.py MOUNT_UDEV`), so libinput only ever sees the host's
existing device properties, a rule written to the container's
`/etc/udev/rules.d/` is invisible to the host's udevd, and
`udevadm control --reload-rules` run inside the container just hangs trying
to reach the host daemon's control socket. The Cage patch avoids touching
udev at all.

## Chromium watchdog and the persistent profile

`rest_server.py` runs a background watchdog coroutine that evaluates a
trivial expression on Chromium's remote-debugging page (`Runtime.evaluate`)
every 30 seconds. On repeated failure it first requests a page reload
(cheaper than restarting the whole container), then, only after three
consecutive 15-second failures plus one final 30-second confirmation, kills
Cage so Supervisor's watchdog restarts the container. All CDP calls run
through a single `asyncio.Lock` (`CDP_LOCK`) because API-triggered
reload/navigation commands and the watchdog's own checks previously opened
competing target WebSockets, which could make a healthy but busy renderer
miss the watchdog's deadline and trigger a false restart.

Chromium keeps a persistent profile under `/data/chromium-profile` so a
login session survives add-on and container restarts. That has a side
effect: a kiosk container never exits cleanly (every stop kills Chromium
mid-flight), so Chromium's profile always records `exit_type: "Crashed"`,
and on the next boot Chromium restores the previous session's tabs on top
of the command-line URL, stacking one more tab per restart. `run.sh` fixes
this on every launch with two steps, both required: it rewrites
`Default/Preferences` with a real JSON edit (`profile.exit_type = "Normal"`,
`profile.exited_cleanly = true`, `session.restore_on_startup = 5`, meaning
"open the New Tab page"), and it deletes the saved tab-session snapshot
files under `Default/Sessions`, which is what Chromium rebuilds stacked
tabs from. Login cookies and per-site Local Storage (including the Browser
Mod ID) live in different files and are left untouched. An earlier
sed-based rewrite of `Preferences` was tried first and did not hold in the
field; the JSON edit replaced it because it is immune to formatting
differences a text substitution can miss.

## Boot ordering: waiting for Home Assistant to serve HTTP

`startup: application` only orders this app after Home Assistant Core has
launched, not after Core is actually serving HTTP; a cold boot can leave
Core unreachable for a minute or more. Chromium does not retry a failed
connection on its own, so without an explicit wait a host reboot used to
land the kiosk on a permanent "connection refused" page. `run.sh` polls the
configured URL with `curl -ks --max-time 3` (any HTTP response counts, `-k`
because the URL may use a self-signed certificate) for up to 5 minutes
before launching the browser regardless, so a genuinely broken URL still
surfaces visibly instead of blocking forever.
