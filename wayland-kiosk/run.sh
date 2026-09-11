#!/usr/bin/env bashio

# Fail before starting services when security settings or trust are invalid.
python3 /app/prepare_runtime.py
unset SUPERVISOR_TOKEN HASSIO_TOKEN

bashio::log.info "================ SYSTEM DIAGNOSTICS ================"

# 1. Check GPU / DRM devices
if [ -d "/dev/dri" ]; then
    bashio::log.info "DRM Device Nodes Found:"
    ls -la /dev/dri
else
    bashio::log.warning "NO /dev/dri DIRECTORY FOUND! GPU passthrough is missing."
fi

# Confirm that the render node and installed VA-API backend work together.
# The Alpine Chromium build enables Linux VA-API decoding, so a successful
# probe here confirms that camera streams can reach the hardware decoder.
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
chown kiosk:kiosk "$XDG_RUNTIME_DIR"

# HOME must be set for Chromium and the launcher wrapper's stat check; see
# docs/operations.md.
export HOME=/root

# Resolve before starting either the API or Chromium. An explicit option wins
# over Supervisor's environment; do not replace a mounted /etc/localtime.
KIOSK_TZ=$(python3 /app/resolve_timezone.py)
if [ -n "$KIOSK_TZ" ]; then
    export TZ="$KIOSK_TZ"
    bashio::log.info "Time zone resolved as ${KIOSK_TZ}; exported as TZ for Chromium."
else
    bashio::log.warning "Could not resolve a named time zone. Set the app time_zone option (for US Central: America/Chicago)."
fi

# Seat management (seatd)
bashio::log.info "Starting seat management daemon..."
export SEATD_SOCK=/run/seatd.sock
export LIBSEAT_BACKEND=seatd
export SEATD_VTBOUND=0  # Tells seatd not to look for a physical TTY/VT

seatd -g kiosk &

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
export WLR_BACKENDS=drm
# Do NOT set WLR_LIBINPUT_NO_DEVICES=1; that disables the touchscreen too.

# Options are read from Supervisor's validated /data/options.json; see
# docs/design.md.
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

URL=$(python3 -c 'import sys; sys.path.insert(0,"/app"); from security_config import effective_ha_url,load_options; print(effective_ha_url(load_options()))')
HA_DASHBOARD=$(read_option 'ha_dashboard' 'lovelace')
BROWSER_REFRESH=$(read_option 'browser_refresh' '12000')
ROTATION_CONFIG=$(read_option 'rotate_display' 'right')
SCREEN_TIMEOUT=$(read_option 'screen_timeout' '0')
AUTH_METHOD=$(read_option 'auth_method' 'none')
AUDIO_ENABLED=$(read_option 'audio_enabled' 'true')
ALLOW_HTTP=$(read_option 'allow_http' 'true')
LOCK_NAVIGATION=$(read_option 'lock_navigation' 'true')
DARK_MODE=$(read_option 'dark_mode' 'true')

# Append the dashboard path (default "lovelace", HA's Overview page).
if [ -n "$HA_DASHBOARD" ]; then
    dashboard_path="${HA_DASHBOARD#/}"
    case "${URL%/}" in
        */"$dashboard_path") ;;
        *) URL="${URL%/}/${dashboard_path}" ;;
    esac
fi
bashio::log.info "Kiosk will load: ${URL}"

# rest_server.py is started further down and inherits the environment as it
# stands at that point, so KIOSK_URL and the lockdown flag have to be
# exported here rather than with the other Chromium exports at the bottom.
export KIOSK_URL="$URL"
export KIOSK_LOCK_NAVIGATION="$LOCK_NAVIGATION"
if [ "$(read_option 'ignore_certificate_errors' 'false')" = "true" ]; then
    bashio::log.warning "Legacy certificate bypass is ignored. To trust a self-signed server, enable allow_self_signed and supply its public PEM certificate."
fi

# Policies apply even when navigation lockdown is off. A write failure
# stops startup rather than launching an unrestricted browser.
python3 /app/browser_policy.py "$URL"

# Dynamic hardware discovery (display & touch). Static fallbacks below are
# used only if auto-discovery fails.
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

# 2. Find the touchscreen device name.
# `|| true` guards against bashio's pipefail turning a SIGPIPE from an
# early `head -n 1` close into a fatal script error; see docs/operations.md.
if [ -f "/proc/bus/input/devices" ]; then
    TOUCH_DEVICE=$(grep -i "Name=" /proc/bus/input/devices | grep -i -E "touch|ilitek" | head -n 1 | cut -d'"' -f2 || true)
fi

if [ -n "$TOUCH_DEVICE" ]; then
    bashio::log.info "Auto-discovered touch device: $TOUCH_DEVICE"
else
    TOUCH_DEVICE="$STATIC_TOUCH"
    bashio::log.warning "Touch auto-discovery failed! Falling back to static: $TOUCH_DEVICE"
fi

# Rotation. Touch-to-output mapping is handled by a Cage compositor patch
# via CAGE_TOUCH_OUTPUT, not udev; see docs/design.md.
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

# Wait for Home Assistant to serve HTTP before launching the browser
# (boot ordering); see docs/design.md.
ha_wait=0
curl_args=(-s --max-time 3 -o /dev/null --proto '=http,https')
if [ -f /data/browser-home/local-trust.pem ]; then
    curl_args+=(--cacert /data/browser-home/local-trust.pem)
fi
until curl "${curl_args[@]}" "$URL"; do
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

# Background services. wait_for_wayland_socket polls for Cage's socket
# rather than using a fixed sleep; see docs/operations.md.
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

# Screen timeout. wlr-randr has no wildcard support; see docs/decisions.md.
(
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

# Credential entry is performed by the API's origin-checked CDP task.
# No username/password is passed to a process argument or keyboard focus.
if [ "$AUTH_METHOD" = "trusted_networks" ]; then
    bashio::log.warning "Bridge networking no longer uses Core's loopback trusted-network rule. Prefer a persistent non-admin login; update Core's trusted-user mapping only for this app's actual address."
fi

# Chromium runtime. Launch flags are explained in docs/operations.md; the
# persistent profile and its exit-state patching are explained in
# docs/design.md and docs/decisions.md.
# Launch the real chromium binary directly, bypassing the launcher wrapper
# and its broken BusyBox stat check; see docs/operations.md.
CHROMIUM_BIN="/usr/lib/chromium/chromium"
if [ ! -x "$CHROMIUM_BIN" ]; then
    bashio::log.warning "${CHROMIUM_BIN} not found -- falling back to the chromium-browser wrapper (expect the BusyBox stat log spam this was meant to avoid)."
    CHROMIUM_BIN="chromium-browser"
fi
export CHROME_DESKTOP="chromium.desktop"
export KIOSK_ROTATION="$ROTATION_CONFIG"
export KIOSK_ROTATION_TRANSFORM="$ROTATION_DEGREES"
export KIOSK_CHROMIUM_BIN="$CHROMIUM_BIN"
export KIOSK_AUDIO_ENABLED="$AUDIO_ENABLED"
export KIOSK_DARK_MODE="$DARK_MODE"

bashio::log.info "Starting Cage with Chromium pointing to: ${URL}"

exec python3 /app/prepare_runtime.py launch /usr/local/bin/cage -s -- /app/launch-browser.sh
