#!/bin/bash

# GrowAssistant Installation Script for Raspberry Pi
# This script automates the complete installation and setup of GrowAssistant
# including Docker, secrets generation, and systemd service creation

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="/opt/grow-assistant"
SERVICE_NAME="grow-assistant"
USER_HOME="$HOME"
CURRENT_USER=$(whoami)
UPDATE_MODE=false

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check if running on supported system
check_system() {
    print_status "Checking system compatibility..."
    
    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_error "This script is designed for Linux systems only."
        exit 1
    fi
    
    # Check if git is available (needed for cloning repo if not already in it)
    if ! command_exists git; then
        print_status "Installing git (required for downloading GrowAssistant)..."
        print_status "Updating package lists..."
        sudo apt-get update
        print_status "Installing git package..."
        sudo apt-get install -y git
        print_success "Git installed successfully"
    fi
    
    print_success "Linux system detected - compatible with GrowAssistant"
}

# Function to install Docker if not present
install_docker() {
    if command_exists docker && (command_exists docker-compose || docker compose version &>/dev/null); then
        print_success "Docker and Docker Compose already installed"
        return 0
    fi
    
    print_status "Installing Docker and Docker Compose..."
    print_warning "Using Docker's convenience script (recommended for development/testing)"
    print_warning "For production deployments, consider manual installation: https://docs.docker.com/engine/install/"
    
    # Use Docker's official convenience script (supports all architectures)
    curl -fSL https://get.docker.com | sudo sh
    
    # Add user to docker group
    sudo usermod -aG docker "$CURRENT_USER"
    
    print_success "Docker installed successfully"
    print_warning "You may need to log out and back in for Docker group membership to take effect"
}

# Function to create installation directory
create_install_dir() {
    print_status "Creating installation directory..."
    
    if [[ ! -d "$INSTALL_DIR" ]]; then
        sudo mkdir -p "$INSTALL_DIR"
        sudo chown "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"
        print_success "Created installation directory: $INSTALL_DIR"
    else
        print_status "Installation directory already exists: $INSTALL_DIR"
    fi
}

# Function to download/copy deployment files
setup_deployment_files() {
    print_status "Setting up deployment files..."
    
    # Backup existing secrets if this is an update
    if [[ "$UPDATE_MODE" == true && -d "$INSTALL_DIR/.secrets" ]]; then
        print_status "Backing up existing secrets..."
        BACKUP_DIR=$(mktemp -d)
        cp -r "$INSTALL_DIR/.secrets" "$BACKUP_DIR/"
        print_success "Secrets backed up to: $BACKUP_DIR"
    fi
    
    # Always ensure we have the latest deployment files from GitHub
    print_status "Ensuring latest deployment files from GitHub..."
    
    # Create temporary directory for fresh clone
    TEMP_REPO_DIR=$(mktemp -d)
    
    # Clone the complete repository to ensure we get everything
    if git clone https://github.com/sabbalot/grow-assistant.git "$TEMP_REPO_DIR"; then
        print_success "Repository cloned successfully"
        
        # Copy entire repository contents to install directory
        cp -r "$TEMP_REPO_DIR"/* "$INSTALL_DIR/"
        
        # Restore secrets if this was an update
        if [[ "$UPDATE_MODE" == true && -d "$BACKUP_DIR/.secrets" ]]; then
            print_status "Restoring existing secrets..."
            cp -r "$BACKUP_DIR/.secrets" "$INSTALL_DIR/"
            rm -rf "$BACKUP_DIR"
            print_success "Existing secrets restored"
        fi
        
        # Clean up temporary directory
        rm -rf "$TEMP_REPO_DIR"
        
        print_success "Latest deployment files downloaded and installed"
    else
        print_error "❌ Failed to clone GrowAssistant repository"
        print_error "This appears to be a network connectivity issue to GitHub."
        echo ""
        print_status "🔧 Troubleshooting suggestions:"
        echo "  1. Check if GitHub is accessible: ping github.com"
        echo "  2. Try a different DNS server: sudo echo 'nameserver 8.8.8.8' >> /etc/resolv.conf"
        echo "  3. Check firewall/router settings"
        echo "  4. Verify your internet connection works: ping 8.8.8.8"
        echo ""
        print_status "📁 For manual installation:"
        echo "  1. Download the repository from another device"
        echo "  2. Transfer everything to: $INSTALL_DIR"
        echo "  3. Re-run this script"
        echo ""
        print_status "🌐 Repository: https://github.com/sabbalot/grow-assistant"
        exit 1
    fi
}

# Function to pull Docker images
pull_docker_images() {
    if [[ "$UPDATE_MODE" == true ]]; then
        print_status "Updating Docker images to latest versions..."
    else
        print_status "Pre-pulling main application images..."
    fi
    
    cd "$INSTALL_DIR"
    
    # Pull the main application images (force latest)
    docker pull phyrron/grow-assistant-app:latest
    docker pull phyrron/grow-assistant-backend:latest
    
    # Pull all images defined in docker-compose.yml
    print_status "Pulling all images from docker-compose.yml..."
    docker compose pull
    
    # Note: Supporting images (PostgreSQL, InfluxDB, etc.) will be pulled automatically by docker compose
    # Note: Grafana and Loki will be built locally from custom Dockerfiles
    
    if [[ "$UPDATE_MODE" == true ]]; then
        print_success "Docker images updated successfully"
    else
        print_success "Main application images pulled successfully"
    fi
}

# Function to generate secrets
generate_secrets() {
    # Skip secret generation in update mode if secrets already exist
    if [[ "$UPDATE_MODE" == true && -d "$INSTALL_DIR/.secrets" ]]; then
        print_status "Update mode: Keeping existing secrets"
        print_success "Existing secrets preserved"
        return 0
    fi
    
    print_status "Generating secrets..."
    
    cd "$INSTALL_DIR"
    
    # Make generate_secrets.sh executable
    chmod +x generate_secrets.sh
    
    # Run secret generation
    ./generate_secrets.sh
    
    print_success "Secrets generated successfully"
}

# Function to start services
start_services() {
    print_status "Starting GrowAssistant services..."
    
    cd "$INSTALL_DIR"
    
    # Stop any existing services to ensure clean start
    print_status "Stopping any existing services..."
    docker compose down 2>/dev/null || true
    
    # Start services
    docker compose up -d
    
    # Wait a bit for services to start
    sleep 10
    
    # Check service status
    if docker compose ps | grep -q "Up"; then
        print_success "Services started successfully"
    else
        print_error "Some services may have failed to start"
        docker compose ps
    fi
}

# Function to create systemd service
create_systemd_service() {
    print_status "Creating systemd service..."
    
    # Create service file
    sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=GrowAssistant IoT Growing Platform
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
User=${CURRENT_USER}
Group=docker

[Install]
WantedBy=multi-user.target
EOF
    
    # Reload systemd and enable service
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    
    print_success "Systemd service created and enabled"
}

# Function to display access information
show_access_info() {
    local ip_address
    ip_address=$(hostname -I | cut -d' ' -f1)
    
    echo ""
    print_success "=== GrowAssistant Installation Complete ==="
    echo ""
    echo "Access your GrowAssistant installation at:"
    echo "  🌐 Web Interface: http://${ip_address}"
    echo "  🌐 Web Interface: http://localhost (if accessing locally)"
    echo "  📊 Grafana Dashboard: http://${ip_address}:3000"
    echo ""
    echo "Service Management:"
    echo "  ▶️  Start:   sudo systemctl start ${SERVICE_NAME}"
    echo "  ⏹️  Stop:    sudo systemctl stop ${SERVICE_NAME}"
    echo "  🔄 Restart: sudo systemctl restart ${SERVICE_NAME}"
    echo "  📊 Status:  sudo systemctl status ${SERVICE_NAME}"
    echo ""
    echo "Manual Docker Commands (from ${INSTALL_DIR}):"
    echo "  ▶️  Start:   docker compose up -d"
    echo "  ⏹️  Stop:    docker compose down"
    echo "  📊 Status:  docker compose ps"
    echo "  📜 Logs:    docker compose logs -f"
    echo ""
    echo "Configuration Files:"
    echo "  📁 Installation: ${INSTALL_DIR}"
    echo "  🔐 Secrets:      ${INSTALL_DIR}/.secrets/"
    echo ""
    echo "Next Steps:"
    echo "  1. Access the web interface to complete initial setup"
    echo "  2. Configure your IoT devices and MQTT settings"
    echo "  3. Set up your growing environment monitoring"
    echo ""
    print_warning "Note: If you added your user to the Docker group, you may need to log out and back in"
}

# Function to check service status
check_services() {
    print_status "Checking service status..."
    
    cd "$INSTALL_DIR"
    
    echo ""
    echo "Docker Compose Services:"
    docker compose ps
    
    echo ""
    echo "Systemd Service Status:"
    sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
}

# Function to detect existing installation
detect_existing_installation() {
    if [[ -d "$INSTALL_DIR" && -f "$INSTALL_DIR/docker-compose.yml" ]]; then
        print_status "Existing GrowAssistant installation detected"
        return 0
    else
        return 1
    fi
}

# Main installation function
main() {
    echo ""
    if [[ "$UPDATE_MODE" == true ]]; then
        print_status "🔄 GrowAssistant Update Script 🔄"
    else
        print_status "🌱 GrowAssistant Installation Script 🌱"
    fi
    echo ""
    
    # Check if running as root
    if [[ $EUID -eq 0 ]]; then
        print_error "This script should not be run as root. Please run as a regular user."
        exit 1
    fi
    
    # Auto-detect update mode if not explicitly set
    if [[ "$UPDATE_MODE" == false ]] && detect_existing_installation; then
        print_status "Existing installation detected. Switching to update mode."
        print_status "Use the --update flag to explicitly run in update mode."
        UPDATE_MODE=true
    fi
    
    # Perform installation/update steps
    check_system
    install_docker
    create_install_dir
    setup_deployment_files
    pull_docker_images
    generate_secrets
    start_services
    create_systemd_service
    
    if [[ "$UPDATE_MODE" == true ]]; then
        echo ""
        print_success "🎉 Update completed successfully! 🎉"
        echo ""
        print_status "Changes applied:"
        echo "  ✅ Latest deployment files downloaded"
        echo "  ✅ Docker images updated to latest versions"
        echo "  ✅ Existing secrets preserved"
        echo "  ✅ Services restarted with new configuration"
    else
        show_access_info
        
        echo ""
        print_status "Running final service check..."
        check_services
        
        echo ""
        print_success "🎉 Installation completed successfully! 🎉"
    fi
}

# Handle script arguments
case "${1:-}" in
    --update)
        UPDATE_MODE=true
        main
        exit 0
        ;;
    --check-status)
        check_services
        exit 0
        ;;
    --help|-h)
        echo "Usage: $0 [OPTIONS]"
        echo ""
        echo "Options:"
        echo "  --update          Update existing GrowAssistant installation"
        echo "  --check-status    Check the status of GrowAssistant services"
        echo "  --help, -h        Show this help message"
        echo ""
        echo "This script will install and configure GrowAssistant on your Raspberry Pi."
        echo ""
        echo "Update Process:"
        echo "  The script automatically detects existing installations and switches to"
        echo "  update mode. It will:"
        echo "    • Download latest deployment files"
        echo "    • Update Docker images to latest versions"
        echo "    • Preserve existing secrets and configuration"
        echo "    • Restart services with new configuration"
        echo ""
        echo "Examples:"
        echo "  $0                    # Fresh installation or auto-detected update"
        echo "  $0 --update          # Explicit update mode"
        echo "  $0 --check-status    # Check service status"
        exit 0
        ;;
    "")
        # No arguments, run main installation
        main
        ;;
    *)
        print_error "Unknown option: $1"
        print_error "Use --help for usage information"
        exit 1
        ;;
esac 