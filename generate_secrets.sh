#!/bin/bash

# Script to generate secrets for the Grow Assistant deployment

SECRETS_DIR=".secrets"

# Function to generate a random alphanumeric string of a given length
generate_random_string() {
  local length=${1:-32} # Default to 32 if no length provided
  LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$length"
}

# Function to generate a random string suitable for passwords (includes special chars)
generate_strong_password() {
  local length=${1:-24} # Default to 24
  # Generate a pool of characters, then shuffle and pick.
  # This is a bit more involved to ensure a mix if openssl isn't available for rand -base64
  # For simplicity here, we'll use a similar method to generate_random_string
  # but you could enhance this to ensure character class variety.
  # Using openssl is more robust for strong passwords if available:
  if command -v openssl &> /dev/null; then
    openssl rand -base64 "$length" | tr -d '/+=' | head -c "$length"
  else
    LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*()_+-=[]{}|;:,.<>?' < /dev/urandom | head -c "$length"
  fi
}

# Create .secrets directory if it doesn't exist
if [ ! -d "$SECRETS_DIR" ]; then
  mkdir -p "$SECRETS_DIR"
  echo "Created directory: $SECRETS_DIR"
else
  echo "Directory $SECRETS_DIR already exists. Checking for existing secret files..."
fi

# Helper function to create a secret file
# Usage: create_secret_file <filename_without_path> <value_or_generator_function> [generator_arg]
create_secret_file() {
  local filename="$1"
  local value_source="$2"
  local generator_arg="$3"
  local full_path="$SECRETS_DIR/$filename"

  if [ -f "$full_path" ]; then
    echo "Secret file $full_path already exists. Skipping generation for this secret."
  else
    local secret_value
    if [[ $(type -t "$value_source") == "function" ]]; then
      if [ -n "$generator_arg" ]; then
        secret_value=$("$value_source" "$generator_arg")
      else
        secret_value=$("$value_source")
      fi
    else
      secret_value="$value_source"
    fi
    echo -n "$secret_value" > "$full_path"
    echo "Generated secret: $filename"
  fi
}

echo "--- Generating Secrets ---"

# InfluxDB Secrets
create_secret_file "influxdb-admin-username" "admin" # Fixed username
create_secret_file "influxdb-admin-password" generate_strong_password 32
create_secret_file "influxdb-admin-token" generate_random_string 40 # InfluxDB tokens are often longer
create_secret_file "influxdb-admin-org" "growassistant" # Default org name
create_secret_file "influxdb-admin-bucket" "env_dev" # Default bucket name

# Grafana Secrets
create_secret_file "grafana-admin-password" "admin"

# Master Key (generic strong secret)
create_secret_file "master_key" generate_random_string 64

# Supervisor Secrets (for your Python backend's internal use)
create_secret_file "supervisor-username" "supervisor_admin"
create_secret_file "supervisor-password" generate_strong_password 32

# PostgreSQL Secrets
create_secret_file "postgres-user" "admin"
create_secret_file "postgres-password" generate_strong_password 32
create_secret_file "postgres-db" "growassistant" # Default database name

echo "--- Secret Generation Complete ---"
echo "Make sure '$SECRETS_DIR/' is listed in your .gitignore file!"
echo "You can now run 'docker compose up -d'."
echo "If you re-run this script and files already exist, they will NOT be overwritten by default."

# Make the script executable
chmod +x generate_secrets.sh 