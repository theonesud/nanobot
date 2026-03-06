#!/bin/bash
set -e

echo "🚀 Starting Nanobot Deployment on EC2..."

# 1. Update and install dependencies
echo "📦 Updating system and installing Docker..."
sudo apt-get update && sudo apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git

# Install Docker
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker "${USER:-$(whoami)}"
    echo "🟢 Docker installed. You might need to log out and back in for group changes, but we'll use sudo for now if needed."
else
    echo "🟢 Docker is already installed"
fi

# 2. Install OpenCode CLI
echo "🛠️ Installing OpenCode CLI..."
if ! command -v opencode &> /dev/null; then
    curl -fsSL https://opencode.ai/install | bash
else
    echo "🟢 OpenCode CLI already installed"
fi

# 3. Setup Nanobot standardized directory
echo "📂 Setting up Nanobot in /opt/nanobot..."
sudo mkdir -p /opt/nanobot/config /opt/nanobot/opencode /opt/nanobot/src
sudo chown -R "${USER:-$(whoami)}":"${USER:-$(whoami)}" /opt/nanobot

cd /opt/nanobot/src
if [ ! -f "docker-compose.yml" ]; then
    echo "⚠️ docker-compose.yml not found. Cloning repo..."
    git clone https://github.com/theonesud/nanobot.git .
fi

# 5. Build and Start Gateway
echo "🏗️ Building and starting Nanobot in Docker..."
docker compose build
docker compose up -d nanobot-gateway

echo "⏳ Waiting for gateway to start..."
sleep 5

# 6. Run Test Command
echo "🤖 Testing Nanobot..."
docker compose run --rm nanobot-cli status

echo ""
echo "✅ Deployment complete!"
echo "📡 Gateway is running on port 18790"
echo "🎮 Use 'docker compose run --rm nanobot-cli agent' for interactive mode."
