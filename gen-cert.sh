#!/usr/bin/env bash
# Generate a TLS cert for the app into ./certs, then restart to serve HTTPS.
#
# Prefers mkcert (https://github.com/FiloSottile/mkcert), which installs a
# local CA so browsers trust the cert with NO warning. Falls back to a
# self-signed cert (browsers warn once; you click through to proceed).
set -euo pipefail

cd "$(dirname "$0")"
CERT_DIR="certs"
mkdir -p "$CERT_DIR"

# Names/IPs the cert should be valid for: localhost, loopback, and this host's
# own hostname + LAN IPs (so you can reach it from other machines too).
HOSTS="localhost 127.0.0.1 ::1"
HOSTS="$HOSTS $(hostname 2>/dev/null || true)"
if command -v hostname >/dev/null 2>&1; then
  HOSTS="$HOSTS $(hostname -I 2>/dev/null || true)"
fi
# Collapse whitespace and drop empties.
read -r -a HOST_ARR <<< "$(echo "$HOSTS" | tr -s ' ')"

if command -v mkcert >/dev/null 2>&1; then
  echo "==> Using mkcert (locally-trusted, no browser warning)"
  # Installing the local CA into the system trust store needs sudo; if that's
  # unavailable, still issue the cert (it works, the browser just warns once
  # until you run 'mkcert -install' yourself).
  mkcert -install || echo "    (could not install local CA — run 'sudo mkcert -install' later for a warning-free cert)"
  mkcert -cert-file "$CERT_DIR/cert.pem" -key-file "$CERT_DIR/key.pem" "${HOST_ARR[@]}"
else
  echo "==> mkcert not found — generating a self-signed cert."
  echo "    Tip: install mkcert for a warning-free, trusted cert."
  # Build a subjectAltName list (DNS: for names, IP: for addresses).
  SAN=""
  for h in "${HOST_ARR[@]}"; do
    if [[ "$h" =~ ^[0-9.]+$ || "$h" == *:* ]]; then SAN="$SAN,IP:$h"; else SAN="$SAN,DNS:$h"; fi
  done
  SAN="${SAN#,}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
    -subj "/CN=localhost" -addext "subjectAltName=$SAN"
fi

chmod 600 "$CERT_DIR/key.pem" 2>/dev/null || true
echo
echo "Cert written to $CERT_DIR/. Set these in .env (then restart):"
echo "    SSL_CERTFILE=/certs/cert.pem"
echo "    SSL_KEYFILE=/certs/key.pem"
echo "Restart with:  podman compose up -d --build"
echo "Then open:     https://localhost:8080"
