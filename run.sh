#!/bin/bash
set -e

# 1. Change directory to script location to ensure correct context
cd "$(dirname "$0")"

# 2. Mode detection (strictly require argument: 'service' or 'test')
RUN_MODE="${_APP_ENV:-$1}"

if [ "$RUN_MODE" != "service" ] && [ "$RUN_MODE" != "test" ]; then
  echo "Usage: $0 {service|test} or set _APP_ENV to 'service' or 'test'"
  exit 1
fi
echo "==> Starting service in [$RUN_MODE] mode..."

# 3. Locate stocktrend-infra directory
INFRA_DIR="$(cd "../stocktrend-infra" && pwd)"

# Env load helper to parse .env securely
load_env_file() {
  local file_path=$1
  if [ -f "$file_path" ]; then
    echo "--> Loading environment variables from $(basename "$file_path")"
    set -a
    . "$file_path"
    set +a
  fi
}

# Load base environment
load_env_file "$INFRA_DIR/secrets/service.env"

# Load test overrides if applicable
if [ "$RUN_MODE" = "test" ]; then
  load_env_file "$INFRA_DIR/secrets/test.env"
fi

# Make sure python path includes stocktrend-provider
export PYTHONPATH=$(pwd)

# Detect python executor (prefer poetry)
if command -v poetry >/dev/null 2>&1; then
  PYTHON="poetry run python"
else
  PYTHON="python3"
  if [ -d ".venv" ]; then
    PYTHON=".venv/bin/python3"
  elif [ -d "venv" ]; then
    PYTHON="venv/bin/python3"
  fi
fi

exec $PYTHON -m src.main
