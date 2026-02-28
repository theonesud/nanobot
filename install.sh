#!/bin/bash
# DO NOT set -e here because sourcing nvm might have minor errors
# set -e

echo "🚀 Starting Nanobot setup..."

# 0. Node Version Management (nvm)
# Source nvm for the script context
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm

if command -v nvm &> /dev/null; then
    echo "🟢 nvm detected. Using version 20 (recommended)..."
    nvm install 20
    nvm use 20
else
    echo "⚠️ nvm not found. Using system node..."
fi

# Exit on error for the rest of the script
set -e

# Check for uv
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed. Please install it first: https://github.com/astral-sh/uv"
    exit 1
fi

# 1. Python dependencies (creates .venv)
echo "🐍 Installing Python dependencies..."
uv sync

# 2. Playwright browsers
echo "🎭 Installing Playwright browsers..."
uv run playwright install --with-deps

# 3. Root Node dependencies
if [ -f "package.json" ]; then
    echo "📦 Installing root Node dependencies..."
    npm install
fi

# 4. Bridge dependencies (WhatsApp)
if [ -d "bridge" ]; then
    echo "🌁 Installing Bridge dependencies..."
    cd bridge
    npm install
    cd ..
fi

echo ""
echo "✅ Setup complete! You can now start the assistant with:"
echo "   uv run nanobot agent"
