#!/bin/bash
set -e

echo "🐳 Building Nanobot Docker image for testing..."
docker build -t nanobot-test .

echo "🏃 Running tests inside Docker container..."
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${HOME}/.nanobot:/root/.nanobot" \
  -v "${HOME}/.opencode:/root/.opencode" \
  -v "$(pwd):$(pwd)" \
  -w "$(pwd)" \
  -e UV_PROJECT_ENVIRONMENT=/usr/local \
  --entrypoint sh \
  nanobot-test \
  -c "uv pip install --system pytest pytest-asyncio httpx && timeout 300 pytest tests/test_nanobot_features.py -v"

echo "✅ Docker-based tests passed!"
