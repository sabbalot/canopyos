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
    
    # Check if we're already in the grow-assistant repo directory
    if [[ -f "docker-compose.yml" ]] && [[ -f "generate_secrets.sh" ]]; then
        # Check if we're already in the target install directory
        if [[ "$(pwd)" == "$INSTALL_DIR" ]]; then
            print_success "Already in installation directory with deployment files"
        else
            print_status "Using deployment files from current directory"
            cp docker-compose.yml "$INSTALL_DIR/"
            cp generate_secrets.sh "$INSTALL_DIR/"
            cp -r mosquitto "$INSTALL_DIR/" 2>/dev/null || true
            cp -r node-red "$INSTALL_DIR/" 2>/dev/null || true
            cp -r grafana "$INSTALL_DIR/" 2>/dev/null || true
            cp -r loki "$INSTALL_DIR/" 2>/dev/null || true
            cp -r promtail "$INSTALL_DIR/" 2>/dev/null || true
            print_success "Copied deployment files from current directory"
        fi
    else
        # We're not in the repo, so clone it to get the files
        print_status "Cloning GrowAssistant repository to get deployment files..."
        
        # Create temporary directory for cloning
        TEMP_REPO_DIR=$(mktemp -d)
        
        # Clone the repository
        if git clone https://github.com/sabbalot/grow-assistant.git "$TEMP_REPO_DIR"; then
            print_success "Repository cloned successfully"
            
            # Copy files from the cloned repo
            cp "$TEMP_REPO_DIR/docker-compose.yml" "$INSTALL_DIR/"
            cp "$TEMP_REPO_DIR/generate_secrets.sh" "$INSTALL_DIR/"
            cp -r "$TEMP_REPO_DIR/mosquitto" "$INSTALL_DIR/" 2>/dev/null || true
            cp -r "$TEMP_REPO_DIR/node-red" "$INSTALL_DIR/" 2>/dev/null || true
            cp -r "$TEMP_REPO_DIR/grafana" "$INSTALL_DIR/" 2>/dev/null || true
            cp -r "$TEMP_REPO_DIR/loki" "$INSTALL_DIR/" 2>/dev/null || true
            cp -r "$TEMP_REPO_DIR/promtail" "$INSTALL_DIR/" 2>/dev/null || true
            
            # Clean up temporary directory
            rm -rf "$TEMP_REPO_DIR"
            
            print_success "Deployment files copied from repository"
        else
            print_error "Failed to clone GrowAssistant repository"
            print_error "Please check your internet connection and try again"
            exit 1
        fi
    fi
}

# Function to pull Docker images
pull_docker_images() {
    print_status "Pre-pulling main application images..."
    
    cd "$INSTALL_DIR"
    
    # Pull the main application images (optional optimization)
    docker pull phyrron/grow-assistant-app:latest
    docker pull phyrron/grow-assistant-backend:latest
    
    # Note: Supporting images (PostgreSQL, InfluxDB, etc.) will be pulled automatically by docker compose
    # Note: Grafana and Loki will be built locally from custom Dockerfiles
    
    print_success "Main application images pulled successfully"
}

# Function to generate secrets
generate_secrets() {
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

# Main installation function
main() {
    echo ""
    print_status "🌱 GrowAssistant Installation Script 🌱"
    echo ""
    
    # Check if running as root
    if [[ $EUID -eq 0 ]]; then
        print_error "This script should not be run as root. Please run as a regular user."
        exit 1
    fi
    
    # Perform installation steps
    check_system
    install_docker
    create_install_dir
    setup_deployment_files
    pull_docker_images
    generate_secrets
    start_services
    create_systemd_service
    
    show_access_info
    
    echo ""
    print_status "Running final service check..."
    check_services
    
    echo ""
    print_success "🎉 Installation completed successfully! 🎉"
}

# Handle script arguments
case "${1:-}" in
    --check-status)
        check_services
        exit 0
        ;;
    --help|-h)
        echo "Usage: $0 [OPTIONS]"
        echo ""
        echo "Options:"
        echo "  --check-status    Check the status of GrowAssistant services"
        echo "  --help, -h        Show this help message"
        echo ""
        echo "This script will install and configure GrowAssistant on your Raspberry Pi."
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