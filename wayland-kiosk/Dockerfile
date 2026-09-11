# Stays on :latest (Alpine edge), not a pinned stable release: the Cage
# build below requires wlroots0.20-dev, which only exists in Alpine edge's
# package index, not in any pinned stable branch (verified against
# pkgs.alpinelinux.org). See docs/decisions.md.
ARG BUILD_FROM=ghcr.io/home-assistant/base:latest@sha256:93ef607824e3f27e868f11b10938283a98bf880ed57bcf8eaa81c6c2d521f6f5
FROM ${BUILD_FROM}

ENV PYTHONDONTWRITEBYTECODE=1

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

# Pull current package-level security patches before installing anything;
# see docs/security.md.
RUN apk upgrade --no-cache

# Install Cage, Chromium, Wayland tools, and Python environment.
# mesa-va-gallium supplies radeonsi_drv_video.so so Chromium can decode
# AMD-accelerated H.264/VP8/VP9 camera streams instead of falling back to CPU.
# libva-utils supplies vainfo for a concise startup capability probe.
# curl provides the Home Assistant readiness probe.
RUN apk add --no-cache \
    cage \
    chromium \
    eudev \
    mesa-dri-gallium \
    mesa-egl \
    mesa-gl \
    mesa-gles \
    mesa-va-gallium \
    libva-utils \
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
    tzdata \
    nss-tools \
    openssl \
    python3 \
    py3-pip

# aiohttp via pip, not the py3-aiohttp apk package: Alpine's packaged version
# lags behind aiohttp's own fixed releases (see docs/security.md). py3-pip
# is already installed above.
RUN pip install --no-cache-dir --break-system-packages aiohttp==3.14.3

# The base image includes tempio, unused by this app and its init scripts.
# Removing it removes its vulnerable embedded Go dependencies instead of hiding findings.
RUN rm -f /usr/bin/tempio

# Build a pinned Cage with a touch-to-output mapping patch; see docs/design.md.
# The upstream Alpine cage package stays installed so it owns runtime deps.
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

# A dedicated desktop user; privileged setup and options remain root-owned.
RUN addgroup -g 1000 kiosk && adduser -D -u 1000 -G kiosk -h /data/browser-home kiosk

# Create policy directories at build time; runtime only writes managed policy files.
RUN mkdir -p /etc/chromium/policies/managed /etc/chromium-browser/policies/managed /etc/opt/chrome/policies/managed

# Copy the execution script and API server into the container
COPY run.sh /run.sh
COPY launch-browser.sh /app/launch-browser.sh
COPY rest_server.py /app/rest_server.py
COPY resolve_timezone.py /app/resolve_timezone.py
COPY security_config.py prepare_runtime.py browser_policy.py credential_login.py network_info.py /app/

# Set execution permissions
RUN chmod a+x /run.sh /app/launch-browser.sh

# Start the script when the container launches
CMD [ "/run.sh" ]
