#!/usr/bin/env bash
#
# One-command start for the Mr. Singh Pizza answering machine (Linux/macOS).
#
#   ./run.sh          Set up (if needed) and start the web/phone server.
#   ./run.sh test     Set up (if needed) and run the test suite.
#   ./run.sh demo     Run the interactive terminal demo (no server).
#
# It creates a virtual environment on first run and installs dependencies,
# so you can just clone the repo and run this.

set -e
cd "$(dirname "$0")"

VENV_DIR="venv"
PYTHON="${PYTHON:-python3}"

# 1. Create the virtual environment on first run.
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment in ./$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
fi

# 2. Activate it.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# 3. Install dependencies (skips quickly if already satisfied).
echo "==> Installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# 4. Load .env if present so credentials/settings are picked up.
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# 5. Do what was asked.
case "${1:-serve}" in
    test)
        echo "==> Running tests"
        pytest
        ;;
    demo)
        echo "==> Starting terminal demo"
        python src/main.py
        ;;
    serve|"")
        PORT="${PORT:-5000}"
        echo "==> Starting server on http://localhost:$PORT"
        echo "    Website:   http://localhost:$PORT/"
        echo "    Kitchen:   http://localhost:$PORT/staff"
        echo "    Press Ctrl+C to stop."
        python src/app.py
        ;;
    *)
        echo "Usage: ./run.sh [serve|test|demo]"
        exit 1
        ;;
esac
