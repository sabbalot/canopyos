FROM python:3.11-slim

# Install Docker CLI
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (static)
ARG DOCKER_CLI_VERSION=26.1.4
RUN set -eux; \
    arch=$(uname -m); \
    case "$arch" in \
      x86_64) dir="x86_64" ;; \
      aarch64) dir="aarch64" ;; \
      armv7l) dir="armhf" ;; \
      *) echo "unsupported arch: $arch" && exit 1 ;; \
    esac; \
    curl -fSL "https://download.docker.com/linux/static/stable/${dir}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
    tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 docker/docker; \
    rm -f /tmp/docker.tgz; \
    docker --version

# Install Docker Compose v2 plugin (arch-aware)
ARG COMPOSE_VERSION=v2.30.3
RUN set -eux; \
    arch=$(uname -m); \
    case "$arch" in \
      x86_64) bin="docker-compose-linux-x86_64" ;; \
      aarch64) bin="docker-compose-linux-aarch64" ;; \
      armv7l) bin="docker-compose-linux-armv7" ;; \
      *) echo "unsupported arch: $arch" && exit 1 ;; \
    esac; \
    mkdir -p /usr/lib/docker/cli-plugins; \
    curl -fSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/${bin}" -o /usr/lib/docker/cli-plugins/docker-compose; \
    chmod +x /usr/lib/docker/cli-plugins/docker-compose

WORKDIR /app

# Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App
COPY app /app/app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7001"]
