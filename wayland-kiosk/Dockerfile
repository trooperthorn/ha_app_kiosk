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
    curl \
    python3 \
    py3-pip \
    py3-requests \
    py3-aiohttp

# Copy the execution script and API server into the container
COPY run.sh /run.sh
COPY rest_server.py /app/rest_server.py

# Set execution permissions
RUN chmod a+x /run.sh

# Start the script when the container launches
CMD [ "/run.sh" ]
