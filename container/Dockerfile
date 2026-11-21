FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV container=docker

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dbus \
        git \
        nano \
        systemd \
        systemd-sysv \
    && rm -rf /var/lib/apt/lists/*

VOLUME ["/sys/fs/cgroup"]

STOPSIGNAL SIGRTMIN+3
LABEL kts.host.image_version="4"

CMD ["/sbin/init"]
