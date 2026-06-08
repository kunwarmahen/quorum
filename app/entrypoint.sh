#!/bin/sh
# Start uvicorn, enabling HTTPS automatically when a cert is mounted.
#
# TLS is opt-in: if a cert + key exist (default /certs/cert.pem and
# /certs/key.pem, overridable via SSL_CERTFILE / SSL_KEYFILE) the app serves
# HTTPS; otherwise it falls back to plain HTTP exactly as before. Generate a
# locally-trusted cert with ./gen-cert.sh on the host.
set -e

PORT="${PORT:-8080}"
CERT="${SSL_CERTFILE:-/certs/cert.pem}"
KEY="${SSL_KEYFILE:-/certs/key.pem}"

set -- main:app --host 0.0.0.0 --port "$PORT"

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
  echo "==> TLS enabled — serving HTTPS on :$PORT (cert: $CERT)"
  set -- "$@" --ssl-certfile "$CERT" --ssl-keyfile "$KEY"
else
  echo "==> No cert found — serving plain HTTP on :$PORT"
  echo "    (run ./gen-cert.sh on the host to enable HTTPS)"
fi

exec uvicorn "$@"
