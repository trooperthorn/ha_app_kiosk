#!/usr/bin/env bashio

# Cage starts its child only after the compositor and Wayland socket exist.
# Applying the output transform here means Chromium creates its first surface
# at the final portrait/landscape size instead of being resized minutes later.
if wlr-randr \
    --output "$KIOSK_OUTPUT" \
    --transform "$KIOSK_ROTATION_TRANSFORM"; then
    bashio::log.info "Applied rotation '${KIOSK_ROTATION}' (transform ${KIOSK_ROTATION_TRANSFORM}) to ${KIOSK_OUTPUT} before browser launch."
else
    bashio::log.warning "Could not apply transform ${KIOSK_ROTATION_TRANSFORM} to ${KIOSK_OUTPUT}; Chromium will still be started."
fi

chromium_args=(
    --kiosk
    --start-fullscreen
    --no-sandbox
    --enable-features=UseOzonePlatform
    --ozone-platform=wayland
    --disable-infobars
    --remote-debugging-port=9222
    --no-first-run
    --no-default-browser-check
    --disable-sync
    --disable-background-networking
    --disable-component-update
    --disable-features=GCM
    --disable-dev-shm-usage
    --disable-session-crashed-bubble
    --hide-crash-restore-bubble
    --user-data-dir=/data/chromium-profile
)

if [ "$KIOSK_IGNORE_CERTIFICATE_ERRORS" = "true" ]; then
    chromium_args+=(--ignore-certificate-errors)
fi

# App mode is deliberate in addition to --kiosk. Cage maximizes its single
# application window; --app removes tabs and the address bar even on Wayland
# builds which ignore Chromium's early fullscreen request.
chromium_args+=("--app=${KIOSK_URL}")

exec "$KIOSK_CHROMIUM_BIN" "${chromium_args[@]}"
