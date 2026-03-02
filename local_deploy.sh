#!/bin/bash
set -e

echo "🚀 Starting Local Persistent Nanobot Deployment (Docker)..."

# 1. Ensure config directories exist
mkdir -p "$HOME/.nanobot" "$HOME/.opencode"

# 2. Build and Start using the static dev config
# Note: ${PWD} and ${HOME} are used inside the yml for path identity.
echo "🏗️ Building Nanobot image..."
docker compose -f docker-compose.dev.yml build

echo "📡 Starting Nanobot Gateway..."
docker compose -f docker-compose.dev.yml up -d nanobot-gateway

echo ""
echo "✅ Local Persistent Deployment Ready!"
echo "------------------------------------"
echo "1. Persistent Config: $HOME/.nanobot"
echo "2. Source Mapped At: $(pwd)"
echo "3. Gateway URL: http://localhost:18790"
echo "4. Interactive Chat:"
echo "   docker compose -f docker-compose.dev.yml run --rm nanobot-cli agent"
echo ""
