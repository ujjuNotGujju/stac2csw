#!/usr/bin/env bash
# Run the CSW endpoint locally on http://127.0.0.1:8000/  (needs: pip install '.[server]')
set -euo pipefail
cd "$(dirname "$0")/.."
export PYCSW_CONFIG="${PYCSW_CONFIG:-$PWD/config/pycsw.cfg}"
export PYCSW_ROOT="$PWD"          # pycsw resolves relative sqlite paths against this
exec gunicorn pycsw.wsgi:application -b "${BIND:-127.0.0.1:8000}" -w 2 --log-level warning
