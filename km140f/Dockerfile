ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.19
FROM $BUILD_FROM

# Bake the add-on version into the image so the value reported to Home
# Assistant always matches the installed add-on. BUILD_VERSION is supplied by
# Supervisor from config.yaml; the fallback keeps local builds working.
ARG BUILD_VERSION=2.4.0
ENV SW_VERSION="${BUILD_VERSION}"

# Install dependencies first for better layer caching
COPY requirements.txt /requirements.txt
RUN pip3 install --no-cache-dir -r /requirements.txt

# Copy application code
COPY km140f.py /km140f.py
COPY rootfs /

# Set permissions
RUN chmod +x /etc/services.d/km140f/run \
 && chmod +x /etc/services.d/km140f/finish

# Docker healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/health', timeout=5)" || exit 1
