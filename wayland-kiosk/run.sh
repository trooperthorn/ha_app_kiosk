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
    bashio::log.error "Supervisor API did not respond after 5 attempts. ALL options will fall back to script defaults for this run -- rotation, login, dashboard, and every other setting will NOT reflect the add-on configuration screen. This is a known Supervisor-side issue (see home-assistant/supervisor#1930); try 'ha supervisor restart', then restart this add-on."
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
# ROTATION + TOUCH CALIBRATION
# ---------------------------------------------------------
# Matrices below are libinput's own documented reference matrices for pure
# clockwise rotation (see libinput's "Static device configuration via udev"
# docs). If your panel is mirrored/flipped on top of being rotated, these
# may still need hand-tuning for your specific hardware -- but they are a
# correct, verified starting point, unlike the previous version's untested
# values.
case "$ROTATION_CONFIG" in
    "right")
        ROTATION_DEGREES="270"
        TOUCH_MATRIX="0 1 0 -1 0 1"
        ;;
    "inverted")
        ROTATION_DEGREES="180"
        TOUCH_MATRIX="-1 0 1 0 -1 1"
        ;;
    "left")
        ROTATION_DEGREES="90"
        TOUCH_MATRIX="0 -1 1 1 0 0"
        ;;
    *)
        ROTATION_DEGREES="normal"
        TOUCH_MATRIX="1 0 0 0 1 0"
        ;;
esac

bashio::log.info "Rotation config '$ROTATION_CONFIG' -> transform $ROTATION_DEGREES, touch matrix [$TOUCH_MATRIX]"

# Write a udev rule mapping the touch device to the active output and
# applying the calibration matrix. This is the mechanism libinput and
# wlroots actually read -- LIBINPUT_CALIBRATION_MATRIX and WL_OUTPUT are
# udev device properties, NOT shell/process environment variables. Setting
# them with `export` (as a previous version of this script did) has no
# effect at all; libinput only sees them if they're attached to the device
# via udev before the device is enumerated.
UDEV_RULE_FILE="/etc/udev/rules.d/99-touch-kiosk.rules"
bashio::log.info "Writing touch calibration udev rule to $UDEV_RULE_FILE ..."
cat > "$UDEV_RULE_FILE" <<EOF
ACTION=="add|change", KERNEL=="event*", ATTRS{name}=="${TOUCH_DEVICE}", ENV{WL_OUTPUT}="${ACTIVE_OUTPUT}", ENV{LIBINPUT_CALIBRATION_MATRIX}="${TOUCH_MATRIX}"
EOF

if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=input 2>/dev/null || true
    bashio::log.info "udev rules reloaded and triggered for input subsystem."
else
    bashio::log.warning "udevadm not found -- touch calibration rule was written but not applied. It will only take effect on next device (re)enumeration."
fi

# ---------------------------------------------------------
# BACKGROUND SERVICES
# ---------------------------------------------------------

# Screen timeout
if [ "$SCREEN_TIMEOUT" -gt 0 ]; then
    bashio::log.info "Setting screen timeout to ${SCREEN_TIMEOUT} seconds..."
    swayidle -w \
        timeout "$SCREEN_TIMEOUT" 'wlr-randr --output \* --off' \
        resume 'wlr-randr --output \* --on' &
else
    bashio::log.info "Screen timeout disabled."
fi

# REST API server
python3 /app/rest_server.py &

# Apply output rotation once Cage's Wayland socket actually exists.
# Polls for up to 15s instead of a fixed sleep, since a fixed sleep can miss
# a slow compositor start (more likely on aarch64/armv7 hardware).
if [ "$ROTATION_DEGREES" != "normal" ]; then
    (
        elapsed=0
        max_wait=15
        wl_display=""
        while [ "$elapsed" -lt "$max_wait" ]; do
            wl_display=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -m 1 "wayland-[0-9]*$" || true)
            if [ -n "$wl_display" ]; then
                break
            fi
            sleep 0.5
            elapsed=$((elapsed + 1))
        done

        if [ -n "$wl_display" ]; then
            export WAYLAND_DISPLAY="$wl_display"
            bashio::log.info "Applying rotation (${ROTATION_DEGREES}) to $ACTIVE_OUTPUT..."
            wlr-randr --output "$ACTIVE_OUTPUT" --transform "$ROTATION_DEGREES"
        else
            bashio::log.error "Wayland socket never appeared after ${max_wait}s -- rotation was NOT applied."
        fi
    ) &
fi

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
CHROMIUM_FLAGS="--kiosk --no-sandbox --enable-features=UseOzonePlatform --ozone-platform=wayland --disable-infobars --remote-debugging-port=9222 --no-first-run --disable-sync"

if bashio::config.true 'ignore_certificate_errors'; then
    CHROMIUM_FLAGS="${CHROMIUM_FLAGS} --ignore-certificate-errors"
fi

# NOTE: --bwsi (guest/ephemeral profile) was removed. It wiped the Chromium
# profile on every restart, which meant any logged-in session never
# persisted -- forcing a fresh auto-login (or trusted_networks bypass) on
# every single container restart. A persistent profile directory under
# /data lets a real login session survive add-on/container restarts.
CHROMIUM_FLAGS="${CHROMIUM_FLAGS} --user-data-dir=/data/chromium-profile"

bashio::log.info "Starting Cage with Chromium pointing to: ${URL}"

exec cage -s -- chromium-browser ${CHROMIUM_FLAGS} "${URL}"
