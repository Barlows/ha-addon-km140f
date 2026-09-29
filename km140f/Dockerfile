ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.19
FROM $BUILD_FROM

COPY requirements.txt /requirements.txt
RUN pip3 install --no-cache-dir -r /requirements.txt

COPY km140f.py /km140f.py
COPY rootfs /

RUN chmod +x /etc/services.d/km140f/run \
 && chmod +x /etc/services.d/km140f/finish
