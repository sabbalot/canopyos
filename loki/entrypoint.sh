#!/bin/sh
set -e

# Change ownership of /tmp/loki
chown -R loki:loki /tmp/loki

# Start Loki with the copied configuration
su loki -s /bin/sh -c 'exec loki -config.file=/etc/loki/local-config.yaml'