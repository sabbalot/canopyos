#!/bin/bash
set -e

echo "Starting Grafana initialization..."

# Path to Docker secrets
SECRETS_DIR="/run/secrets"

# Read InfluxDB credentials from Docker secrets
if [ -f "${SECRETS_DIR}/influxdb-admin-token" ]; then
  INFLUXDB_ADMIN_TOKEN=$(cat "${SECRETS_DIR}/influxdb-admin-token")
  echo "InfluxDB Admin Token loaded."
else
  echo "InfluxDB admin token secret not found!" >&2
  exit 1
fi

if [ -f "${SECRETS_DIR}/influxdb-admin-bucket" ]; then
  INFLUXDB_BUCKET=$(cat "${SECRETS_DIR}/influxdb-admin-bucket")
  echo "InfluxDB Bucket loaded."
else
  echo "InfluxDB bucket secret not found!" >&2
  exit 1
fi

if [ -f "${SECRETS_DIR}/influxdb-admin-org" ]; then
  INFLUXDB_ORG=$(cat "${SECRETS_DIR}/influxdb-admin-org")
  echo "InfluxDB Organization loaded."
else
  echo "InfluxDB organization secret not found!" >&2
  exit 1
fi

# Ensure the provisioning directory and datasources subdirectory exist and have correct permissions
echo "Ensuring Grafana provisioning directories have correct permissions..."
mkdir -p /etc/grafana/provisioning/datasources
chown -R 472:472 /etc/grafana/provisioning

# Create influxdb.yaml directly in the datasources folder
echo "Creating influxdb.yaml with provided credentials..."
cat <<EOF > /etc/grafana/provisioning/datasources/influxdb.yaml
apiVersion: 1

datasources:
  - name: influxdb_v2
    type: influxdb
    access: proxy
    url: http://influxdb:8086
    jsonData:
      version: Flux
      organization: ${INFLUXDB_ORG}
      defaultBucket: ${INFLUXDB_BUCKET}
      tlsSkipVerify: true
    secureJsonData:
      token: ${INFLUXDB_ADMIN_TOKEN}
EOF

echo "InfluxDB datasource configured successfully."

# Start Grafana
echo "Starting Grafana..."
exec /run.sh "$@"