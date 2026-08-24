#!/usr/bin/env bashio

bashio::log.info "================ SYSTEM DIAGNOSTICS ================"

# 1. Check GPU / DRM devices
if [ -d "/dev/dri" ]; then
    bashio::log.info "DRM Device Nodes Found:"
    ls -la /dev/dri
else
    bashio::log.warning "NO /dev/dri DIRECTORY FOUND! GPU passthrough is missing."
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
# WAIT FOR SUPERVISOR API TO BE READY
# ---------------------------------------------------------
# bashio::config talks to the Supervisor API (/addons/self/options/config).
# Right after an add-on rebuild/restart, this call can transiently return
# 403 Forbidden while Supervisor finishes registering the new container
# instance -- a known, previously-reported Supervisor-side issue (see
# home-assistant/supervisor#1930 and #4111), not something this add-on can
# fully prevent. When it happens, EVERY bashio::config call fails at once,
# so every option below falls back to its script-level default -- including
# rotate_display -- which can look like a rotation bug but isn't. Retry a
# canary read a few times with backoff before accepting the fallback path;
# this resolves the common transient case automatically.
SUPERVISOR_READY=0
for attempt in $(seq 1 5); do
    canary=$(bashio::config 'ha_url' 2>/dev/null || true)
    if [ -n "$canary" ] && [ "$canary" != "null" ]; then
        SUPERVISOR_READY=1
        break
    fi
    bashio::log.warning "Supervisor API not ready yet (attempt ${attempt}/5), retrying in 2s..."
    sleep 2
done

if [ "$SUPERVISOR_READY" -eq 0 ]; then
    bashio::log.error "Supervisor API did not respond after 5 attempts. Options will fall back to script defaults for now -- but the rotation and screen-timeout tasks will keep polling and pick up the real values if the API recovers within ~5 minutes. If the API never recovers for an entire run: one-off -> known transient Supervisor issue (home-assistant/supervisor#1930), try 'ha supervisor restart' then restart this add-on; every run -> likely a stale add-on registration/token (this add-on's slug changed from haos_wayland_kiosk to app_kiosk), which needs a full UNINSTALL + reinstall so Supervisor issues a fresh token."
fi

# ---------------------------------------------------------
# READ UI OPTIONS (WITH FALLBACKS)
# ---------------------------------------------------------
# Reuse the canary read above instead of fetching ha_url a second time.
URL="$canary"
ROTATION_CONFIG=$(bashio::config 'rotate_display')
SCREEN_TIMEOUT=$(bashio::config 'screen_timeout')
AUTH_METHOD=$(bashio::config 'auth_method')
HA_USERNAME=$(bashio::config 'ha_username')
HA_PASSWORD=$(bashio::config 'ha_password')
LOGIN_DELAY=$(bashio::config 'login_delay')

if [ -z "$URL" ] || [ "$URL" == "null" ]; then
    URL="http://127.0.0.1:8123"
    bashio::log.warning "HA API returned blank URL. Forcing default to http://127.0.0.1:8123"
fi

if [ -z "$SCREEN_TIMEOUT" ] || [ "$SCREEN_TIMEOUT" == "null" ]; then
    SCREEN_TIMEOUT=600
    bashio::log.warning "HA API returned blank timeout. Forcing 600s."
fi

if [ -z "$ROTATION_CONFIG" ] || [ "$ROTATION_CONFIG" == "null" ]; then
    ROTATION_CONFIG="normal"
fi

if [ -z "$AUTH_METHOD" ] || [ "$AUTH_METHOD" == "null" ]; then
    AUTH_METHOD="trusted_networks"
fi

if [ -z "$LOGIN_DELAY" ] || [ "$LOGIN_DELAY" == "null" ]; then
    LOGIN_DELAY=10
fi

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
# Touch input needs NO separate calibration here. wlroots (Cage's backend)
# maps touchscreens to the output and applies the output's transform to
# touch coordinates, so `wlr-randr --transform` below rotates display and
# touch together.
#
# A previous version tried to write a udev rule setting
# LIBINPUT_CALIBRATION_MATRIX / WL_OUTPUT and reload udev. That mechanism
# cannot work inside this add-on: `udev: true` bind-mounts the HOST's
# /run/udev database read-only (Supervisor docker/const.py MOUNT_UDEV), so
# libinput reads only the host's device properties. A rule written to the
# container's /etc/udev/rules.d/ is invisible to the host's udevd, and
# `udevadm control --reload-rules` in here just hangs trying to reach the
# host daemon's control socket (the "Terminated timeout 5 udevadm" log
# line). If a specific panel ever needs a hand-tuned matrix, that has to
# happen through the compositor or on the host -- not via container udev.
rotation_to_transform() {
    case "$1" in
        "right")    echo "270" ;;
        "inverted") echo "180" ;;
        "left")     echo "90" ;;
        *)          echo "normal" ;;
    esac
}

ROTATION_DEGREES=$(rotation_to_transform "$ROTATION_CONFIG")

bashio::log.info "Rotation config '$ROTATION_CONFIG' -> output transform $ROTATION_DEGREES (touch follows the output transform automatically under wlroots)"

# The Supervisor API has been observed rejecting this add-on's token for a
# window around startup (403 on every bashio::config call) and then
# accepting the very same token minutes later. When that happens, the
# option reads above all fall back to script defaults. Rather than losing
# rotation and screen-timeout for the whole run, the background tasks below
# re-read their option once the compositor is up, polling until the API
# recovers (or ~5 minutes pass -- the recovery window has been observed to
# exceed one minute in the field, and a kiosk rotating late beats never).
# Echoes the freshest value; bashio logs its own errors to stderr, so
# command substitution captures only the value.
reread_option_if_api_was_down() {
    local name="$1"
    local current="$2"
    local val=""
    local i=0
    if [ "$SUPERVISOR_READY" -eq 1 ]; then
        echo "$current"
        return 0
    fi
    while [ "$i" -lt 60 ]; do
        val=$(bashio::config "$name" 2>/dev/null || true)
        if [ -n "$val" ] && [ "$val" != "null" ]; then
            echo "$val"
            return 0
        fi
        sleep 5
        i=$((i + 1))
    done
    echo "$current"
}

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
    local elapsed=0
    local max_wait=15
    local wl_display=""
    while [ "$elapsed" -lt "$max_wait" ]; do
        wl_display=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -m 1 "wayland-[0-9]*$" || true)
        if [ -n "$wl_display" ]; then
            echo "$wl_display"
            return 0
        fi
        sleep 0.5
        elapsed=$((elapsed + 1))
    done
    return 1
}

# Screen timeout. Spawned unconditionally: if the Supervisor API was down
# at boot, SCREEN_TIMEOUT holds the fallback (600), and the subshell
# re-reads the real value once the API recovers before deciding.
#
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
    timeout_final=$(reread_option_if_api_was_down 'screen_timeout' "$SCREEN_TIMEOUT")
    if [ "$timeout_final" != "$SCREEN_TIMEOUT" ]; then
        bashio::log.info "Supervisor API recovered -- screen_timeout is ${timeout_final}s (boot-time fallback was ${SCREEN_TIMEOUT}s)."
    fi
    if [ "$timeout_final" -gt 0 ] 2>/dev/null; then
        bashio::log.info "Setting screen timeout to ${timeout_final} seconds on ${ACTIVE_OUTPUT}..."
        exec swayidle -w \
            timeout "$timeout_final" "wlr-randr --output $ACTIVE_OUTPUT --off" \
            resume "wlr-randr --output $ACTIVE_OUTPUT --on"
    else
        bashio::log.info "Screen timeout disabled."
    fi
) &

# REST API server. KIOSK_OUTPUT tells it which output its display_on/off
# commands should target (wlr-randr has no wildcard).
export KIOSK_OUTPUT="$ACTIVE_OUTPUT"
python3 /app/rest_server.py &

# Apply output rotation once Cage's Wayland socket actually exists.
# Spawned unconditionally for the same API-recovery reason as swayidle:
# if options fell back at boot, rotation looked like "normal" here even
# though the user configured e.g. "right".
(
    wl_display=$(wait_for_wayland_socket) || {
        bashio::log.error "Wayland socket never appeared after 15s -- rotation was NOT applied."
        exit 0
    }
    export WAYLAND_DISPLAY="$wl_display"
    rotation_final=$(reread_option_if_api_was_down 'rotate_display' "$ROTATION_CONFIG")
    transform=$(rotation_to_transform "$rotation_final")
    if [ "$transform" != "normal" ]; then
        bashio::log.info "Applying rotation '${rotation_final}' (transform ${transform}) to $ACTIVE_OUTPUT..."
        wlr-randr --output "$ACTIVE_OUTPUT" --transform "$transform"
    else
        bashio::log.info "Rotation '${rotation_final}' -> no output transform to apply."
    fi
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
                    page_url=$(curl -s --max-time 2 http://localhost:9222/json 2>/dev/null | grep -o '"url":"[^"]*"' | head -n 1 || true)
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
CHROMIUM_FLAGS="--kiosk --no-sandbox --enable-features=UseOzonePlatform --ozone-platform=wayland --disable-infobars --remote-debugging-port=9222 --no-first-run --disable-sync --disable-background-networking --disable-component-update --disable-features=GCM --disable-dev-shm-usage"

if bashio::config.true 'ignore_certificate_errors'; then
    CHROMIUM_FLAGS="${CHROMIUM_FLAGS} --ignore-certificate-errors"
fi

# NOTE: --bwsi (guest/ephemeral profile) was removed. It wiped the Chromium
# profile on every restart, which meant any logged-in session never
# persisted -- forcing a fresh auto-login (or trusted_networks bypass) on
# every single container restart. A persistent profile directory under
# /data lets a real login session survive add-on/container restarts.
CHROMIUM_FLAGS="${CHROMIUM_FLAGS} --user-data-dir=/data/chromium-profile"

# The persistent profile has a side effect: a kiosk container never exits
# cleanly (every stop kills Chromium mid-flight), so the profile records
# exit_type "Crashed". On the next boot Chromium then RESTORES the previous
# session's tabs on top of the command-line URL -- accumulating one more
# tab per restart and breaking out of clean kiosk presentation (visible
# address bar, the URL stacked in multiple tabs). Standard kiosk fix:
# rewrite the previous session as cleanly exited before every launch, and
# suppress the crash-restore machinery via flags.
CHROMIUM_PREFS="/data/chromium-profile/Default/Preferences"
if [ -f "$CHROMIUM_PREFS" ]; then
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' "$CHROMIUM_PREFS" 2>/dev/null || true
    sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/' "$CHROMIUM_PREFS" 2>/dev/null || true
fi
CHROMIUM_FLAGS="${CHROMIUM_FLAGS} --disable-session-crashed-bubble --hide-crash-restore-bubble"

bashio::log.info "Starting Cage with Chromium pointing to: ${URL}"

exec cage -s -- chromium-browser ${CHROMIUM_FLAGS} "${URL}"
