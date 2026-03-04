#!/bin/bash
set -e

DOCKER=false
VERBOSE=""
TIMEOUT=120

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --docker     Build and run tests inside a Docker container via local_deploy.sh"
    echo "  -v           Verbose pytest output"
    echo "  -h, --help   Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0              Run all tests locally"
    echo "  $0 --docker     Deploy to Docker and run tests in the container"
    echo "  $0 -v           Run locally with verbose output"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --docker) DOCKER=true; shift ;;
        -v)       VERBOSE="-v"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [ "$DOCKER" = true ]; then
    echo "🐳 Docker mode: deploying via local_deploy.sh..."
    bash ./local_deploy.sh

    echo ""
    echo "🧪 Running tests inside Docker container..."
    docker compose -f docker-compose.dev.yml run --rm --no-deps --entrypoint sh nanobot-cli -c \
        'export PATH="$HOME/.local/bin:$PATH" && \
         pip install -q pytest pytest-asyncio pytest-timeout httpx 2>&1 && \
         pytest tests/ '"$VERBOSE"' --timeout='"$TIMEOUT"' 2>&1'

    EXIT_CODE=$?
    echo ""
    if [ $EXIT_CODE -eq 0 ]; then
        echo "✅ All Docker tests passed!"
    else
        echo "❌ Some Docker tests failed (exit code $EXIT_CODE)"
    fi
    exit $EXIT_CODE
fi

echo "🔍 Running local checks..."
echo ""

# 1. Ensure dev dependencies
echo "📦 Installing dev dependencies..."
uv sync --extra dev --quiet

# 2. Ruff
echo "🧹 Running ruff..."
uv run ruff check . --fix --unsafe-fixes
echo "   ✅ ruff passed"

# 3. Vulture
echo "💀 Running vulture..."
uv run vulture nanobot
echo "   ✅ vulture passed"

# 4. Pytest
echo "🧪 Running pytest..."
uv run pytest tests/ $VERBOSE --timeout=$TIMEOUT

EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ All local tests passed!"
else
    echo "❌ Some tests failed (exit code $EXIT_CODE)"
fi
exit $EXIT_CODE
