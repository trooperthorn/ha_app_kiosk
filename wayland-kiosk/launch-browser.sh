#!/usr/bin/env bashio

# Apply rotation before Chromium's first surface, so it starts at final size.
if wlr-randr \
    --output "$KIOSK_OUTPUT" \
    --transform "$KIOSK_ROTATION_TRANSFORM"; then
    bashio::log.info "Applied rotation '${KIOSK_ROTATION}' (transform ${KIOSK_ROTATION_TRANSFORM}) to ${KIOSK_OUTPUT} before browser launch."
else
    bashio::log.warning "Could not apply transform ${KIOSK_ROTATION_TRANSFORM} to ${KIOSK_OUTPUT}; Chromium will still be started."
fi

# Retain exit diagnostics without creating unbounded native core files.
ulimit -c 0

chromium_args=(
    --kiosk
    --start-fullscreen
    --enable-features=UseOzonePlatform
    --ozone-platform=wayland
    --disable-infobars
    --remote-debugging-address=127.0.0.1
    --remote-debugging-port=9222
    --no-first-run
    --no-default-browser-check
    --disable-sync
    --disable-background-networking
    --disable-component-update
    --disable-notifications
    --disable-push-api-background-mode
    --disable-gpu-shader-disk-cache
    --disable-dev-shm-usage
    --disable-session-crashed-bubble
    --disable-pinch
    --hide-crash-restore-bubble
    --user-data-dir=/data/chromium-profile
)

if [ "${KIOSK_AUDIO_ENABLED:-true}" != "true" ]; then
    chromium_args+=(--mute-audio)
fi

# Chromium's time zone comes from the TZ environment variable, which run.sh
# resolves and exports before this script runs (see run.sh); Chromium
# inherits it like any other child process. No extra flag is needed here,
# and none exists: Chromium has no supported --timezone switch outside its
# own test harnesses.

# Dark mode. --force-dark-mode makes the browser report
# `prefers-color-scheme: dark`, which is the signal Home Assistant's own
# theme handling follows. Blink's auto-darkening filter
# (--enable-features=WebContentsForceDark) is deliberately NOT used: it
# inverts the page's own colors and fights a frontend that already has a
# dark theme. See docs/decisions.md.
if [ "$KIOSK_DARK_MODE" = "true" ]; then
    chromium_args+=(--force-dark-mode)
fi

# Navigation lockdown is enforced by the managed policy run.sh writes; this
# flag is the belt-and-braces half of it, refusing target=_blank and
# window.open outright so no unreachable second window can ever be created.
if [ "$KIOSK_LOCK_NAVIGATION" = "true" ]; then
    chromium_args+=(--block-new-web-contents)
fi

# --app (with --kiosk) removes tabs/address bar even if fullscreen is ignored.
chromium_args+=("--app=${KIOSK_URL}")

exec "$KIOSK_CHROMIUM_BIN" "${chromium_args[@]}"
