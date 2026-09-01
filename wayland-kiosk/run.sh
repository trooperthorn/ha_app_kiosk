#!/usr/bin/env bashio

bashio::log.info "================ SYSTEM DIAGNOSTICS ================"

# 1. Check GPU / DRM devices
if [ -d "/dev/dri" ]; then
    bashio::log.info "DRM Device Nodes Found:"
    ls -la /dev/dri
else
    bashio::log.warning "NO /dev/dri DIRECTORY FOUND! GPU passthrough is missing."
fi

# Confirm that the render node and installed VA-API backend work together.
# Chromium 152 enables Linux VA-API decoding by default, so a successful
# probe here means camera streams can use the hardware decoder.
if [ -c "/dev/dri/renderD128" ] && command -v vainfo >/dev/null 2>&1; then
    if vaapi_info=$(vainfo --display drm --device /dev/dri/renderD128 2>&1); then
        vaapi_driver=$(printf '%s\n' "$vaapi_info" | sed -n 's/.*Driver version: //p' | head -n 1)
        if [ -n "$vaapi_driver" ]; then
            bashio::log.info "VA-API hardware video decoding available: $vaapi_driver"
        else
            bashio::log.info "VA-API hardware video decoding available."
        fi
    else
        bashio::log.warning "VA-API probe failed; Chromium camera streams will fall back to CPU decoding."
        printf '%s\n' "$vaapi_info" | sed 's/^/[vainfo] /'
    fi
fi

# 2. Scan physical display connectors in /sys/class/drm
bashio::log.info "Scanning Connected Displays:"
if [ -d "/sys/class/drm" ]; then
    for status_file in /sys/class/drm/*/status; do
        if [ -f "$status_file" ]; then
            card=$(echo "$status_file" | cut -d'/' -f5)
            status=$(cat "$status_file")
            bashio::log.info "  Output Connector [$card]: $status"
        fi
    done
else
    bashio::log.warning "No /sys/class/drm connector entries found!"
fi

# 3. Check Input devices
if [ -d "/dev/input" ]; then
    bashio::log.info "Input Devices Found:"
    ls -la /dev/input
fi

bashio::log.info "===================================================="
bashio::log.info "Configuring Wayland runtime environment..."

export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"

# HOME is not set in the s6/bashio environment. Two reasons to export it:
# 1. Chromium itself expects HOME for its NSS certificate store and dconf
#    lookups when running as root.
# 2. The chromium-browser wrapper (still used as the runtime FALLBACK when
#    /usr/lib/chromium/chromium is missing, see the launch section) checks
#    `stat -c %u -L ${XDG_CONFIG_HOME:-${HOME}}`. With HOME empty that runs
#    with no file argument at all, which is what makes BusyBox stat print
#    its whole usage text into the log, and the surrounding
#    `[ $(...) -eq 0 ]` test then collapses to `[ -eq 0 ]` (the
#    "sh: 0: unknown operand" line). With HOME set, even the fallback path
#    is clean. Our explicit --user-data-dir still wins either way because
#    Chromium takes the last occurrence of a repeated switch.
export HOME=/root

# ---------------------------------------------------------
# SEAT MANAGEMENT (seatd)
# ---------------------------------------------------------
bashio::log.info "Starting seat management daemon..."
export SEATD_SOCK=/run/seatd.sock
export LIBSEAT_BACKEND=seatd
export SEATD_VTBOUND=0  # Tells seatd not to look for a physical TTY/VT

seatd -g root &

for i in $(seq 1 10); do
    if [ -S "$SEATD_SOCK" ]; then
        bashio::log.info "Seatd socket successfully established."
        break
    fi
    sleep 0.3
done

if [ ! -S "$SEATD_SOCK" ]; then
    bashio::log.error "CRITICAL: seatd failed to create socket!"
fi

unset DISPLAY
unset WAYLAND_DISPLAY
export WLR_BACKEND=drm
# NOTE: do NOT set WLR_LIBINPUT_NO_DEVICES=1 here -- that disables libinput
# entirely (including the touchscreen), which was the cause of an earlier
# bug in this add-on.

# ---------------------------------------------------------
# READ UI OPTIONS LOCALLY
# ---------------------------------------------------------
# Supervisor writes the validated add-on options to /data/options.json before
# starting the container. Reading that file avoids the /addons/self API race
# which returned 403 at boot and silently discarded every configured value.
OPTIONS_FILE="/data/options.json"

read_option() {
    local key="$1"
    local fallback="$2"
    python3 - "$OPTIONS_FILE" "$key" "$fallback" <<'PYEOF'
import json
import sys

path, key, fallback = sys.argv[1:4]
try:
    with open(path, encoding="utf-8") as options_file:
        value = json.load(options_file).get(key, fallback)
except Exception:
    value = fallback

if value is None:
    value = fallback
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PYEOF
}

if python3 -m json.tool "$OPTIONS_FILE" >/dev/null 2>&1; then
    bashio::log.info "Loaded add-on configuration from ${OPTIONS_FILE}."
else
    bashio::log.warning "${OPTIONS_FILE} is missing or invalid; documented defaults will be used."
fi

URL=$(read_option 'ha_url' 'http://127.0.0.1:8123')
HA_DASHBOARD=$(read_option 'ha_dashboard' 'lovelace')
BROWSER_REFRESH=$(read_option 'browser_refresh' '12000')
ROTATION_CONFIG=$(read_option 'rotate_display' 'right')
SCREEN_TIMEOUT=$(read_option 'screen_timeout' '0')
AUTH_METHOD=$(read_option 'auth_method' 'trusted_networks')
HA_USERNAME=$(read_option 'ha_username' '')
HA_PASSWORD=$(read_option 'ha_password' '')
LOGIN_DELAY=$(read_option 'login_delay' '10')
IGNORE_CERTIFICATE_ERRORS=$(read_option 'ignore_certificate_errors' 'true')

# Append the dashboard path so the kiosk lands on the configured dashboard
# (the default "lovelace" is Home Assistant's Overview page) instead of
# whatever Core decides to serve at the bare URL.
if [ -n "$HA_DASHBOARD" ]; then
    dashboard_path="${HA_DASHBOARD#/}"
    case "${URL%/}" in
        */"$dashboard_path") ;;
        *) URL="${URL%/}/${dashboard_path}" ;;
    esac
fi
bashio::log.info "Kiosk will load: ${URL}"

# ---------------------------------------------------------
# DYNAMIC HARDWARE DISCOVERY (Display & Touch)
# ---------------------------------------------------------
# Known static fallbacks, used only if auto-discovery fails:
STATIC_DISPLAY="DP-1"
STATIC_TOUCH="ILITEK ILITEK-TP"

ACTIVE_OUTPUT=""
TOUCH_DEVICE=""

# 1. Find the active connected monitor
for status_file in /sys/class/drm/*/status; do
    if [ -f "$status_file" ] && [ "$(cat "$status_file")" = "connected" ]; then
        raw_card=$(echo "$status_file" | cut -d'/' -f5)
        ACTIVE_OUTPUT=$(echo "$raw_card" | sed 's/^card[0-9]*-//')
        break
    fi
done

if [ -n "$ACTIVE_OUTPUT" ]; then
    bashio::log.info "Auto-discovered active display: $ACTIVE_OUTPUT"
else
    ACTIVE_OUTPUT="$STATIC_DISPLAY"
    bashio::log.warning "Display auto-discovery failed! Falling back to static: $ACTIVE_OUTPUT"
fi

# 2. Find the touchscreen device name
#
# NOTE ON `|| true`: bashio runs every script with `set -o errexit -o
# pipefail` plus `shopt -s inherit_errexit`. Under that combination, a
# pipeline like `... | head -n 1` is a landmine: head reads its one line
# and exits, and if the upstream grep is still writing when that happens,
# grep gets SIGPIPE -- which pipefail treats as the pipeline's exit status,
# which errexit then treats as a fatal script error (this is exactly what
# produces a container exit code of 141 = 128+SIGPIPE). Whether it triggers
# depends on output size vs. the pipe buffer, which is why it can pass
# testing and then fail intermittently in the field. `|| true` doesn't
# change what gets captured -- the text is already on stdout regardless --
# it just stops the harmless early-close from being treated as fatal.
if [ -f "/proc/bus/input/devices" ]; then
    TOUCH_DEVICE=$(grep -i "Name=" /proc/bus/input/devices | grep -i -E "touch|ilitek" | head -n 1 | cut -d'"' -f2 || true)
fi

if [ -n "$TOUCH_DEVICE" ]; then
    bashio::log.info "Auto-discovered touch device: $TOUCH_DEVICE"
else
    TOUCH_DEVICE="$STATIC_TOUCH"
    bashio::log.warning "Touch auto-discovery failed! Falling back to static: $TOUCH_DEVICE"
fi

# ---------------------------------------------------------
# ROTATION
# ---------------------------------------------------------
# wlroots applies the output transform to touch coordinates only after the
# input device is mapped to that output. Many USB panels (including ILITEK)
# do not expose a WL_OUTPUT udev property, and Cage otherwise logs that the
# device cannot be mapped. The image contains a small Cage patch which maps
# unassigned input devices to CAGE_TOUCH_OUTPUT once that connector appears.
#
# A previous version tried to write a udev rule setting
# LIBINPUT_CALIBRATION_MATRIX / WL_OUTPUT and reload udev. That mechanism
# cannot work inside this add-on: `udev: true` bind-mounts the HOST's
# /run/udev database read-only (Supervisor docker/const.py MOUNT_UDEV), so
# libinput reads only the host's device properties. A rule written to the
# container's /etc/udev/rules.d/ is invisible to the host's udevd, and
# `udevadm control --reload-rules` in here just hangs trying to reach the
# host daemon's control socket (the "Terminated timeout 5 udevadm" log
# line). CAGE_TOUCH_OUTPUT supplies the same mapping at the compositor layer
# without attempting to modify Home Assistant OS's host udev database.
rotation_to_transform() {
    case "$1" in
        "right")    echo "270" ;;
        "inverted") echo "180" ;;
        "left")     echo "90" ;;
        *)          echo "normal" ;;
    esac
}

ROTATION_DEGREES=$(rotation_to_transform "$ROTATION_CONFIG")

bashio::log.info "Rotation config '$ROTATION_CONFIG' -> output transform $ROTATION_DEGREES"
export CAGE_TOUCH_OUTPUT="$ACTIVE_OUTPUT"

# ---------------------------------------------------------
# WAIT FOR HOME ASSISTANT TO SERVE HTTP (boot ordering)
# ---------------------------------------------------------
# `startup: application` means Supervisor starts this add-on after Core is
# launched, but at a cold boot Core can take a minute or more before it
# actually serves HTTP. Chromium does not retry a failed connection on its
# own, so without this wait a host reboot landed the kiosk on a dead
# "connection refused" error page until someone restarted the add-on.
# Any HTTP response counts as "serving" (an auth redirect or 404 is fine);
# -k because ha_url may use a self-signed certificate. Bounded at 5
# minutes, then the browser launches anyway so a broken URL still surfaces
# visibly instead of blocking forever.
ha_wait=0
until curl -ks --max-time 3 -o /dev/null "$URL"; do
    ha_wait=$((ha_wait + 5))
    if [ "$ha_wait" -ge 300 ]; then
        bashio::log.warning "No HTTP response from ${URL} after 300s. Launching the browser anyway -- it may show a connection error until Home Assistant is reachable."
        break
    fi
    if [ $((ha_wait % 30)) -eq 0 ]; then
        bashio::log.info "Waiting for Home Assistant at ${URL} to serve HTTP (${ha_wait}s elapsed)..."
    fi
    sleep 5
done
if [ "$ha_wait" -lt 300 ]; then
    bashio::log.info "Home Assistant is serving HTTP at ${URL} (waited ${ha_wait}s)."
fi

# ---------------------------------------------------------
# BACKGROUND SERVICES
# ---------------------------------------------------------

# Cage only creates its Wayland socket after it starts, so anything that
# needs WAYLAND_DISPLAY (swayidle, the rotation call below) has to wait for
# it rather than assume it's already there. Polls for up to 15s instead of
# a fixed sleep, since a fixed sleep can miss a slow compositor start (more
# likely on aarch64/armv7 hardware). Echoes the discovered socket name and
# returns non-zero on timeout so callers can each log their own context.
wait_for_wayland_socket() {
    local wl_display=""
    for _attempt in $(seq 1 30); do
        wl_display=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -m 1 "wayland-[0-9]*$" || true)
        if [ -n "$wl_display" ]; then
            echo "$wl_display"
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# Screen timeout.
# NOTE: wlr-randr has NO wildcard support -- `--output *` fails with
# "unknown output *" (a previous version did exactly that, so blanking
# never worked). The real connector name must be used.
(
    # swayidle connects to Wayland at startup, so it must wait for Cage's
    # socket -- started immediately it fails instantly with "Unable to
    # connect to the compositor" (a previous version's bug).
    wl_display=$(wait_for_wayland_socket) || {
        bashio::log.error "Wayland socket never appeared after 15s -- screen timeout (swayidle) was NOT started."
        exit 0
    }
    export WAYLAND_DISPLAY="$wl_display"
    if [ "$SCREEN_TIMEOUT" -gt 0 ] 2>/dev/null; then
        bashio::log.info "Setting screen timeout to ${SCREEN_TIMEOUT} seconds on ${ACTIVE_OUTPUT}..."
        exec swayidle -w \
            timeout "$SCREEN_TIMEOUT" "wlr-randr --output $ACTIVE_OUTPUT --off" \
            resume "wlr-randr --output $ACTIVE_OUTPUT --on"
    else
        bashio::log.info "Screen timeout disabled."
    fi
) &

# REST API server. It must inherit the compositor's WAYLAND_DISPLAY or all
# wlr-randr/wtype endpoints fail even though the HTTP server itself is up.
export KIOSK_OUTPUT="$ACTIVE_OUTPUT"
(
    wl_display=$(wait_for_wayland_socket) || {
        bashio::log.error "Wayland socket never appeared after 15s -- control API was NOT started."
        exit 0
    }
    export WAYLAND_DISPLAY="$wl_display"
    exec python3 /app/rest_server.py
) &

# Periodic browser refresh. Zero disables it; positive values are seconds.
if [ "$BROWSER_REFRESH" -gt 0 ] 2>/dev/null; then
    (
        wl_display=$(wait_for_wayland_socket) || exit 0
        export WAYLAND_DISPLAY="$wl_display"
        while sleep "$BROWSER_REFRESH"; do
            if ! wtype -k F5; then
                bashio::log.warning "Periodic browser refresh failed."
            fi
        done
    ) &
    bashio::log.info "Periodic browser refresh enabled every ${BROWSER_REFRESH}s."
else
    bashio::log.info "Periodic browser refresh disabled."
fi

# Log what the browser actually has open -- the "address bar" contents --
# so display-side issues (stacked tabs, unexpected redirects, login pages)
# can be diagnosed from the add-on log without a photo of the screen.
# Samples Chromium's DevTools target list at ~30s and ~90s after launch;
# one healthy kiosk boot shows exactly one page.
(
    for wait_s in 30 60; do
        sleep "$wait_s"
        curl -s --max-time 3 http://127.0.0.1:9222/json 2>/dev/null | python3 -c "
import json, sys
try:
    targets = json.load(sys.stdin)
except Exception:
    sys.exit(0)
pages = [t.get('url', '?') for t in targets if t.get('type') == 'page']
print('Browser reports %d open page(s) (address bar contents):' % len(pages))
for url in pages:
    print('  ' + url)
" | while IFS= read -r line; do
            bashio::log.info "$line"
        done || true
    done
) &

# ---------------------------------------------------------
# LOGIN HANDLING
# ---------------------------------------------------------
# trusted_networks: nothing to do here. HA Core must have a
# trusted_networks auth provider configured (in Core's own
# configuration.yaml) trusting 127.0.0.1 / ::1, with allow_bypass_login: true.
# This add-on cannot configure that for you -- it doesn't have (and
# shouldn't need) access to Core's config.
#
# credentials: type the username/password into the HA login form once it
# appears, detected by polling Chromium's CDP endpoint rather than blind-
# typing after a fixed delay.
case "$AUTH_METHOD" in
    "trusted_networks")
        bashio::log.info "Using trusted_networks auth. Ensure Home Assistant Core's configuration.yaml has a matching trusted_networks provider for 127.0.0.1."
        ;;
    "credentials")
        if [ -z "$HA_USERNAME" ] || [ -z "$HA_PASSWORD" ]; then
            bashio::log.warning "auth_method is 'credentials' but ha_username/ha_password are not both set -- skipping auto-login."
        else
            (
                wl_display=$(wait_for_wayland_socket) || {
                    bashio::log.warning "Wayland socket unavailable -- skipping credential auto-login."
                    exit 0
                }
                export WAYLAND_DISPLAY="$wl_display"

                # login_delay is schema'd as float(0,), so it may arrive as
                # e.g. "10.5" -- bash's [ -lt ] only does integers, so this
                # truncates to whole seconds for the loop bound.
                login_delay_int="${LOGIN_DELAY%.*}"
                if [ -z "$login_delay_int" ]; then
                    login_delay_int=10
                fi

                elapsed=0
                found=0
                while [ "$elapsed" -lt "$login_delay_int" ]; do
                    page_url=$(curl -s --max-time 2 http://localhost:9222/json 2>/dev/null | python3 -c '
import json, sys
try:
    pages = json.load(sys.stdin)
    print(next((p.get("url", "") for p in pages if p.get("type") == "page"), ""))
except Exception:
    pass
' || true)
                    if echo "$page_url" | grep -q "auth/authorize"; then
                        found=1
                        break
                    fi
                    sleep 1
                    elapsed=$((elapsed + 1))
                done

                if [ "$found" -eq 1 ]; then
                    bashio::log.info "Login page detected, submitting credentials..."
                    sleep 1  # let the page finish rendering/focusing the username field
                    wtype "$HA_USERNAME"
                    wtype -k Tab
                    wtype "$HA_PASSWORD"
                    wtype -k Return
                else
                    bashio::log.warning "Login page not detected within ${login_delay_int}s -- skipping auto-login. Consider raising login_delay."
                fi
            ) &
        fi
        ;;
    "none")
        bashio::log.info "auth_method is 'none' -- no login handling will be attempted."
        ;;
esac

# ---------------------------------------------------------
# CHROMIUM RUNTIME
# ---------------------------------------------------------
# --disable-background-networking / --disable-component-update /
# --disable-features=GCM: stop Chromium's Google Cloud Messaging and
# update phone-home attempts, which are useless on a kiosk and spam the
# log with "Registration response error message: DEPRECATED_ENDPOINT /
# PHONE_REGISTRATION_ERROR" (see chromiumembedded/cef#4078 for the same
# fix). Deliberately NOT using --log-level=3 to hide them instead --
# ERROR-level output has repeatedly been the diagnostic signal for real
# bugs in this add-on.
# --disable-dev-shm-usage: Docker gives containers a 64MB /dev/shm by
# default; Chromium rendering a large dashboard can exceed it and crash
# tabs. This makes Chromium use /tmp instead.
# NOTE: --bwsi (guest/ephemeral profile) was removed. It wiped the Chromium
# profile on every restart, which meant any logged-in session never
# persisted -- forcing a fresh auto-login (or trusted_networks bypass) on
# every single container restart. A persistent profile directory under
# /data lets a real login session survive add-on/container restarts.
# The persistent profile has a side effect: a kiosk container never exits
# cleanly (every stop kills Chromium mid-flight), so the profile records
# exit_type "Crashed". On the next boot Chromium then RESTORES the previous
# session's tabs on top of the command-line URL -- accumulating one more
# tab per restart and breaking out of clean kiosk presentation (visible
# address bar, the URL stacked in multiple tabs).
#
# Fix, two layers, both required (an earlier sed-based rewrite of the
# Preferences file was observed NOT to stop the stacking in the field):
# 1. Patch the profile's exit state to "clean" with a real JSON edit
#    (python3, not sed -- immune to formatting differences).
# 2. Delete the saved tab-session snapshots outright. A kiosk never wants
#    tab restore, and these files are what Chromium rebuilds stacked tabs
#    from. Login cookies and site Local Storage (the Browser Mod ID) live
#    in different files and are untouched.
CHROMIUM_PROFILE="/data/chromium-profile"
CHROMIUM_PREFS="${CHROMIUM_PROFILE}/Default/Preferences"
if [ -f "$CHROMIUM_PREFS" ]; then
    python3 - "$CHROMIUM_PREFS" <<'PYEOF' || bashio::log.warning "Could not rewrite Chromium exit state -- session restore may stack tabs."
import json, sys
path = sys.argv[1]
with open(path) as f:
    prefs = json.load(f)
profile = prefs.setdefault("profile", {})
profile["exit_type"] = "Normal"
profile["exited_cleanly"] = True
# Belt and suspenders: per Chromium's documented RestoreOnStartup values
# (1 = restore last session, 4 = open a URL list, 5 = open the New Tab
# page), pin 5 so no restore path has anything to re-open; the kiosk URL
# comes from the command line regardless.
session = prefs.setdefault("session", {})
session["restore_on_startup"] = 5
with open(path, "w") as f:
    json.dump(prefs, f)
PYEOF
fi
rm -rf "${CHROMIUM_PROFILE}/Default/Sessions" 2>/dev/null || true

# Launching the REAL chromium binary at its full path, not `chromium` /
# `chromium-browser` on PATH -- on this Alpine image BOTH of those are
# just symlinks to the same chromium-launcher.sh wrapper script
# (confirmed in the aports APKBUILD: chromium-browser -> chromium-launcher.sh,
# chromium -> chromium-browser), so calling either still runs the wrapper.
# That wrapper's own root-user safety check --
#   [ $(id -u) -eq 0 ] && [ $(stat -c %u -L "$HOME") -eq 0 ]
# -- calls `stat -L`, a flag this image's BusyBox stat applet does not
# support (its own usage text lists only `[-ltf] [-c FMT]`, no -L). The
# broken stat call dumps BusyBox's full usage/help text into the log on
# every single boot, immediately followed by a "sh: 0: unknown operand"
# from the now-empty `$(...)` feeding the `-eq 0` test -- both harmless
# (the wrapper's own final `exec "$PROGDIR/chromium" ...` still runs
# Chromium either way) but pure noise. The wrapper's only functional
# purpose is to auto-add --user-data-dir when the caller didn't supply
# one, so root doesn't hit Chromium's own refusal to run with a
# root-owned profile dir -- we already pass --user-data-dir explicitly
# above, so calling the real binary directly (the exact path the wrapper
# itself resolves $PROGDIR to and execs) skips the wrapper, and its
# broken check, with no loss of behavior. CHROME_DESKTOP is set to match
# what the wrapper would have set, for parity with anything that reads it
# (About page branding, etc).
CHROMIUM_BIN="/usr/lib/chromium/chromium"
if [ ! -x "$CHROMIUM_BIN" ]; then
    # Safety net in case a future package revision moves the binary:
    # fall back to the wrapper rather than fail to start the kiosk over a
    # log-spam cosmetic issue.
    bashio::log.warning "${CHROMIUM_BIN} not found -- falling back to the chromium-browser wrapper (expect the BusyBox stat log spam this was meant to avoid)."
    CHROMIUM_BIN="chromium-browser"
fi
export CHROME_DESKTOP="chromium.desktop"
export KIOSK_URL="$URL"
export KIOSK_ROTATION="$ROTATION_CONFIG"
export KIOSK_ROTATION_TRANSFORM="$ROTATION_DEGREES"
export KIOSK_CHROMIUM_BIN="$CHROMIUM_BIN"
export KIOSK_IGNORE_CERTIFICATE_ERRORS="$IGNORE_CERTIFICATE_ERRORS"

bashio::log.info "Starting Cage with Chromium pointing to: ${URL}"

exec /usr/local/bin/cage -s -- /app/launch-browser.sh
