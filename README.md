# 🌱 GrowAssistant - IoT Growing Automation Platform

**Transform consumer IoT devices into intelligent, enterprise-level growing systems with plug-and-play setup.**

[![Docker Pulls](https://img.shields.io/docker/pulls/phyrron/grow-assistant-backend.svg)](https://hub.docker.com/r/phyrron/grow-assistant-backend)
[![Docker Pulls](https://img.shields.io/docker/pulls/phyrron/grow-assistant-app.svg)](https://hub.docker.com/r/phyrron/grow-assistant-app)

---

## 🚀 Quick Start

**One-command installation:**

```bash
curl -fsSL https://raw.githubusercontent.com/sabbalot/grow-assistant/main/install-grow-assistant.sh | bash
```

**Access your installation at:** `http://your-device-ip`

*For alternative installation methods, see the [Installation Methods](#-installation-methods) section below.*

---

## 📋 What You Get

- **🌿 Complete Growing Platform** - Monitor and automate your growing environment
- **🏠 IoT Device Integration** - Support for Zigbee sensors, smart plugs, and more
- **📊 Real-time Monitoring** - Temperature, humidity, soil moisture tracking
- **🤖 Intelligent Automation** - Data-driven growing optimization
- **🔗 MQTT Integration** - Connect with Home Assistant and other platforms
- **📱 Modern Web Interface** - Access from any device
- **🔐 Secure by Default** - Security best practices

---

## 🛠️ Prerequisites

### Hardware Requirements

**Minimum (Basic Setup):**
- Any Linux system with ARM64 or AMD64 architecture
- 2GB RAM (works on Raspberry Pi 4, desktops, cloud instances)
- 16GB storage (SD card minimum, SSD/NVMe preferred)
- Network connection (Ethernet/WiFi)

**Recommended (Production):**
- 4GB+ RAM (Raspberry Pi 5, modern desktop, or cloud instance)
- 64GB+ SSD storage (much better performance than SD cards)
- Gigabit Ethernet connection

### Software Requirements

- **Docker** and **Docker Compose** (auto-installed by script)
- **Git** (for manual installation)
- Linux-based OS (Raspberry Pi OS, Ubuntu, Debian)

---

## 🔧 Installation Methods

### Method 1: Automated Installation (Recommended)

The installation script handles everything automatically:

- Docker installation and configuration
- Repository cloning and file setup
- Secure secret generation  
- Service deployment
- Systemd service creation
- System integration

**One-command:**
```bash
curl -fsSL https://raw.githubusercontent.com/sabbalot/grow-assistant/main/install-grow-assistant.sh | bash
```

### Method 2: Manual Installation

For users who prefer full control over each step:

```bash
# 1. Install Docker (if not already installed)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Log out and back in after adding user to docker group

# 2. Clone repository
git clone https://github.com/sabbalot/grow-assistant.git
cd grow-assistant

# 3. Generate secrets
bash ./generate_secrets.sh

# 4. Start services
docker compose up -d

# 5. Verify installation
docker compose ps
```

**For production docker deployments, consider:**
- Manual Docker installation following [official docs](https://docs.docker.com/engine/install/ubuntu/)
- Setting up external storage for data volumes

---

## 🖥️ Service Management

### Systemd Service (Automatic Startup)

The installation script creates a systemd service for automatic startup:

```bash
# Service management
sudo systemctl start grow-assistant      # Start services
sudo systemctl stop grow-assistant       # Stop services  
sudo systemctl restart grow-assistant    # Restart services
sudo systemctl status grow-assistant     # Check status

# Enable/disable automatic startup
sudo systemctl enable grow-assistant     # Enable auto-start
sudo systemctl disable grow-assistant    # Disable auto-start
```

#### Manual Systemd Service Creation

If you prefer to create the systemd service manually, create the file `/etc/systemd/system/grow-assistant.service`:

```ini
[Unit]
Description=GrowAssistant IoT Growing Platform
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/grow-assistant
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
User=your-username
Group=docker

[Install]
WantedBy=multi-user.target
```

**To create it manually:**

```bash
# Create the service file (adjust paths and username)
sudo nano /etc/systemd/system/grow-assistant.service

# Reload systemd and enable the service
sudo systemctl daemon-reload
sudo systemctl enable grow-assistant
sudo systemctl start grow-assistant
```

**Important:** Replace `/opt/grow-assistant` with your actual installation directory and `your-username` with your actual username.

### Manual Docker Management

For direct Docker control:

```bash
# Navigate to installation directory
cd /opt/grow-assistant  # (or your chosen directory)

# Service management
docker compose up -d      # Start services in background
docker compose down       # Stop and remove services
docker compose restart    # Restart services
docker compose ps         # Check service status
docker compose logs -f    # View real-time logs
```

---

## 🌐 Accessing Your Installation

After installation, access the platform at:

- **Web Interface:** `http://your-device-ip`
- **Local Access:** `http://localhost` (if accessing from the device itself)

### Default Services

- **Main Application:** Port 80 (HTTP)
- **Grafana Dashboard:** Port 3000 (HTTP)
- **Grow Assistant API:** Port 8000 (localhost only)
- **InfluxDB:** Port 8086 (localhost only)

---

## ⚙️ Configuration

### Environment Setup

1. **Access the frontend** at `http://your-device-ip`
2. **Complete initial setup** following the on-screen wizard
3. **Start GrowAssistant MQTT Broker through Frontend** for your IoT devices
4. **Start zigbee2mqtt through Frontend** to enable zigbee device paring -> check "Devices" section for an integrated pairing UI or use the zigbee2mqtt frontend
4. **Add sensors and devices** through the device management interface
5. **Set up automation rules** based on your growing needs
6. **Access Grafana dashboards** at `http://your-device-ip:3000` (admin/generated-password)
7. **Download pre-built dashboards** via the web interface from our remote dashboard library

### IoT Device Integration

GrowAssistant supports a wide range of IoT devices:

- **Zigbee Sensors:** Temperature, humidity, soil moisture
- **Smart Plugs:** For pumps, lights, fans, heaters
- **MQTT Devices:** Any device supporting MQTT protocol
- **Home Assistant:** Seamless integration via MQTT

### MQTT Topic Structure

```javascript
// Sensor data format
{
    "data_type": "temperature",
    "value": 23.5,
    "sensorID": "greenhouse_01"
}

 //See MQTT Topic Structure Documentation for more details on how to publish your HomeAssistant or custom build devices. 
```

---

## 🔍 Monitoring and Troubleshooting

### Health Checks

```bash
# Check all services
docker compose ps

# View logs
docker compose logs -f

# Check individual service logs
docker compose logs app
docker compose logs backend
docker compose logs influxdb
docker compose logs postgres
```

### Common Issues

**Services not starting:**
```bash
# Check Docker status
sudo systemctl status docker

# Restart Docker if needed
sudo systemctl restart docker

# Restart GrowAssistant
docker compose down && docker compose up -d
```

**Web interface not accessible:**
- Verify services are running: `docker compose ps`
- Check firewall settings
- Ensure port 80 is not used by other services

**Database issues:**
```bash
# Check PostgreSQL logs
docker compose logs postgres

# Reset database (⚠️ data loss)
docker compose down
docker volume rm grow-assistant_postgres-data
docker compose up -d
```

---
## 🔧 Advanced Configuration

### Custom Installation Directory

```bash
# Install to custom location
export INSTALL_DIR="/home/pi/grow-assistant"
./install-grow-assistant.sh
```

### Resource Limitations

For constrained systems, modify `docker-compose.yml`:

```yaml
services:
  app:
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M
```

---

## 🆙 Updates

### Automatic Updates (Recommended)

**Use the same installation command for updates:**

```bash
# The script automatically detects existing installations and updates them
curl -fsSL https://raw.githubusercontent.com/sabbalot/grow-assistant/main/install-grow-assistant.sh | bash
```

**Or use explicit update mode:**

```bash
# Explicitly run in update mode
curl -fsSL https://raw.githubusercontent.com/sabbalot/grow-assistant/main/install-grow-assistant.sh | bash -s -- --update
```

**What happens during updates:**
- ✅ Downloads latest deployment files from GitHub
- ✅ Updates Docker images to latest versions  
- ✅ Preserves existing secrets and configuration
- ✅ Restarts services with new configuration
- ✅ Shows clear update completion status

### Manual Docker Updates

For manual control over Docker image updates:

```bash
# Navigate to installation directory
cd /opt/grow-assistant  # (or your installation directory)

# Pull latest images and restart
docker compose pull
docker compose up -d
```

### Check Installation Status

```bash
# Check current service status
curl -fsSL https://raw.githubusercontent.com/sabbalot/grow-assistant/main/install-grow-assistant.sh | bash -s -- --check-status
```

---

## 📊 System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **Architecture** | linux/arm64 or linux/amd64 | linux/arm64 or linux/amd64 |
| **CPU** | 1GHz+ (ARM/x86) | 1.8GHz+ multi-core |
| **RAM** | 2GB | 4GB+ |
| **Storage** | 16GB | 64GB+ SSD |
| **Network** | 100Mbps | 1Gbps |

---

## 🤝 Support and Community

### Getting Help

- **Documentation:** Comprehensive guides in the web interface
- **Issues:** [GitHub Issues](https://github.com/sabbalot/grow-assistant/issues)
- **Discussions:** [GitHub Discussions](https://github.com/sabbalot/grow-assistant/discussions)
- **Discord**: [GrowAssistant Discord](https://discord.gg/m7KAwWpq)

### Contributing

- **Bug Reports:** Use GitHub Issues with detailed information
- **Feature Requests:** Propose new features via GitHub Discussions or on Discord
- **Documentation:** Help improve our guides and tutorials

---

### Deployment Configuration (This Repository)

- **Docker Compose files**: Free to use, modify, and share
- **Documentation**: Free to use and improve
- **Setup scripts**: Free to adapt for your needs
- **No warranty**: Provided "as-is" for convenience

---

### Third-Party Components
| Component | License | Source |
|-----------|---------|---------|
| **Grafana Stack** | AGPL-3.0 | https://github.com/grafana |
| **InfluxDB** | MIT | https://github.com/influxdata |
| **PostgreSQL** | PostgreSQL License | https://postgresql.org |

*Happy Growing! 🌱*
