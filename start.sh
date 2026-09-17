#!/usr/bin/env bash
# Bring up the Quorum stack and make sure the Ollama model is available.
#
# The container is managed by systemd via the quadlet at
#   ~/.config/containers/systemd/quorum.container
# so it also comes back automatically after a reboot. This script builds the
# image and hands over to systemd; it deliberately does NOT run
# "podman compose up", which would fight the unit for the name "quorum" and
# port 7080.
set -euo pipefail

cd "$(dirname "$0")"

IMAGE="localhost/quorum-app:latest"
UNIT="quorum.service"
DATA_DIR="${HOME}/MeetingMinutes"

mkdir -p "${DATA_DIR}/recordings"

if [[ ! -f .env ]]; then
  echo "==> No .env found, creating one from .env.example"
  cp .env.example .env
  echo "    Edit .env to set OBS_PASSWORD before recording."
fi

# Load OLLAMA_MODEL from .env (default llama3.2).
OLLAMA_MODEL="$(grep -E '^OLLAMA_MODEL=' .env 2>/dev/null | cut -d= -f2- || true)"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.2}"

echo "==> Checking host Ollama on :11434"
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  if curl -s http://localhost:11434/api/tags | grep -q "\"${OLLAMA_MODEL}\""; then
    echo "    Ollama up, model '${OLLAMA_MODEL}' present."
  else
    echo "    Ollama up, but model '${OLLAMA_MODEL}' not found. Pull it with:"
    echo "        ollama pull ${OLLAMA_MODEL}"
  fi
else
  echo "    WARNING: Ollama not reachable on localhost:11434."
  echo "    Start it ('ollama serve') before generating minutes."
fi

echo "==> Building ${IMAGE}"
podman build -t "${IMAGE}" ./app

echo "==> Restarting ${UNIT}"
# Picks up edits to the quadlet, and clears the failed state from any earlier
# crash so a restart isn't refused.
systemctl --user daemon-reload
systemctl --user reset-failed "${UNIT}" 2>/dev/null || true
systemctl --user restart "${UNIT}"

# The unit is Type=notify; if it went active the container is up.
if ! systemctl --user is-active --quiet "${UNIT}"; then
  echo
  echo "ERROR: ${UNIT} did not start. Recent logs:"
  journalctl --user -u "${UNIT}" -n 30 --no-pager
  exit 1
fi

# HTTPS is on whenever a cert exists in ./certs (see ./gen-cert.sh).
if [[ -f certs/cert.pem && -f certs/key.pem ]]; then
  URL="https://localhost:7080"
else
  URL="http://localhost:7080"
  echo
  echo "    Tip: browsers may auto-upgrade http://localhost to https and fail."
  echo "    Run ./gen-cert.sh to serve trusted HTTPS, or use http://127.0.0.1:7080"
fi

echo
echo "All set. Open the dashboard:  ${URL}"
echo "Make sure OBS is running on the host with the WebSocket server enabled."
echo
echo "  logs:    journalctl --user -u ${UNIT} -f"
echo "  stop:    systemctl --user stop ${UNIT}"
echo "  status:  systemctl --user status ${UNIT}"
