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

# HOME must be set for Chromium and the launcher wrapper's stat check; see
# docs/operations.md.
export HOME=/root

# Seat management (seatd)
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

# Append the dashboard path (default "lovelace", HA's Overview page).
if [ -n "$HA_DASHBOARD" ]; then
    dashboard_path="${HA_DASHBOARD#/}"
    case "${URL%/}" in
        */"$dashboard_path") ;;
        *) URL="${URL%/}/${dashboard_path}" ;;
    esac
fi
bashio::log.info "Kiosk will load: ${URL}"

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

# Log the browser's open pages for diagnosis; see docs/operations.md.
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

# Login handling. trusted_networks requires Core-side configuration this
# app cannot set itself; see wayland-kiosk/DOCS.md. credentials types the
# login form once detected via CDP polling rather than a blind fixed delay.
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

                # Truncate to whole seconds; bash [ -lt ] needs an integer
                # but login_delay is float(0,). See docs/operations.md.
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

# Chromium runtime. Launch flags are explained in docs/operations.md; the
# persistent profile and its exit-state patching are explained in
# docs/design.md and docs/decisions.md.
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
# 5 = RestoreOnStartup "open the New Tab page"; see docs/design.md.
session = prefs.setdefault("session", {})
session["restore_on_startup"] = 5
with open(path, "w") as f:
    json.dump(prefs, f)
PYEOF
fi
rm -rf "${CHROMIUM_PROFILE}/Default/Sessions" 2>/dev/null || true

# Launch the real chromium binary directly, bypassing the launcher wrapper
# and its broken BusyBox stat check; see docs/operations.md.
CHROMIUM_BIN="/usr/lib/chromium/chromium"
if [ ! -x "$CHROMIUM_BIN" ]; then
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
