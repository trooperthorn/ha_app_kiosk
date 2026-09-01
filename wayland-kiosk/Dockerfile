ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM ${BUILD_FROM}

# Install Cage, Chromium, Wayland tools, and Python environment
# curl added: run.sh polls Chromium's CDP endpoint (http://localhost:9222/json)
# to detect the HA login page before typing credentials.
RUN apk add --no-cache \
    cage \
    chromium \
    eudev \
    mesa-dri-gallium \
    mesa-egl \
    mesa-gl \
    mesa-gles \
    seatd \
    wayland \
    font-noto \
    font-noto-emoji \
    wlr-randr \
    wtype \
    wlopm \
    swayidle \
    xwayland \
    curl \
    python3 \
    py3-pip \
    py3-requests \
    py3-aiohttp

# Cage only transforms touchscreen coordinates after the input device has
# been mapped to an output. USB panels commonly omit the WL_OUTPUT udev
# property, and Home Assistant OS exposes its host udev database read-only
# to add-ons. Build a pinned Cage with a narrow patch that maps unassigned
# inputs to the connector supplied through CAGE_TOUCH_OUTPUT once that
# output is initialized. The upstream Alpine cage package remains installed
# so it owns all matching runtime dependencies.
ARG CAGE_VERSION=0.3.1
ARG CAGE_SHA512=b220767df979e4315233643d8285c9a26925684f95ef1d0c3ad87e05042598c33e630ba960a5569e3ee971a2e86ff0f44ac7361f03dcc5dee78995faa8fa1189
COPY cage-map-unassigned-input.patch /tmp/cage-map-unassigned-input.patch
RUN apk add --no-cache --virtual .cage-build-deps \
        build-base \
        dbus \
        meson \
        ninja \
        patch \
        scdoc \
        wayland-protocols \
        wlroots0.20-dev \
    && curl -fsSL \
        "https://github.com/cage-kiosk/cage/archive/refs/tags/v${CAGE_VERSION}.tar.gz" \
        -o /tmp/cage.tar.gz \
    && echo "${CAGE_SHA512}  /tmp/cage.tar.gz" | sha512sum -c - \
    && mkdir -p /tmp/cage-src \
    && tar -xzf /tmp/cage.tar.gz -C /tmp/cage-src --strip-components=1 \
    && patch -d /tmp/cage-src -p1 < /tmp/cage-map-unassigned-input.patch \
    && meson setup /tmp/cage-src/build /tmp/cage-src \
        --prefix=/usr/local \
        --buildtype=release \
        -Db_lto=true \
    && meson compile -C /tmp/cage-src/build \
    && meson install -C /tmp/cage-src/build \
    && apk del .cage-build-deps \
    && rm -rf /tmp/cage-src /tmp/cage.tar.gz /tmp/cage-map-unassigned-input.patch

# Copy the execution script and API server into the container
COPY run.sh /run.sh
COPY launch-browser.sh /app/launch-browser.sh
COPY rest_server.py /app/rest_server.py

# Set execution permissions
RUN chmod a+x /run.sh /app/launch-browser.sh

# Start the script when the container launches
CMD [ "/run.sh" ]
