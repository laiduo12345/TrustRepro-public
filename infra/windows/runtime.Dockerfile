FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3
ARG DOCKER_VERSION=5:29.9.2-1~debian.12~bookworm
ARG COMPOSE_VERSION=5.6.1-1~debian.12~bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg systemd systemd-sysv dbus iptables iproute2 \
    procps python3 openssl gzip tar \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable' > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends \
       docker-ce=${DOCKER_VERSION} docker-ce-cli=${DOCKER_VERSION} containerd.io \
       docker-compose-plugin=${COMPOSE_VERSION} \
    && systemctl enable docker \
    && rm -rf /var/lib/apt/lists/* /var/log/*
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends libpam-systemd dbus-user-session \
    && rm -rf /var/lib/apt/lists/*
COPY infra/windows/runtime/wsl.conf /etc/wsl.conf
COPY infra/windows/runtime/bootstrap.py /usr/local/lib/trustrepro/bootstrap.py
COPY infra/windows/runtime/trustreproctl /usr/local/bin/trustreproctl
RUN chmod 755 /usr/local/bin/trustreproctl \
    && mkdir -p /var/lib/trustrepro/releases /etc/trustrepro \
    && echo 1 > /etc/trustrepro/runtime-version
CMD ["/sbin/init"]
