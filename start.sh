#!/usr/bin/env bash
# Bring up the Meeting Minutes stack with Podman and make sure the Ollama
# model is available.
set -euo pipefail

cd "$(dirname "$0")"

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

echo "==> Building and starting the app container"
podman compose up -d --build

# HTTPS is on whenever a cert exists in ./certs (see ./gen-cert.sh).
if [[ -f certs/cert.pem && -f certs/key.pem ]]; then
  URL="https://localhost:8080"
else
  URL="http://localhost:8080"
  echo
  echo "    Tip: browsers may auto-upgrade http://localhost to https and fail."
  echo "    Run ./gen-cert.sh to serve trusted HTTPS, or use http://127.0.0.1:8080"
fi

echo
echo "All set. Open the dashboard:  ${URL}"
echo "Make sure OBS is running on the host with the WebSocket server enabled."
