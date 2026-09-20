#!/usr/bin/env bash
# Install only. No source-data API, model request, scheduled job, or port opening.
set -euo pipefail
DEPLOY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$DEPLOY_ROOT"
umask 077
if [ "$(id -u)" -eq 0 ]; then
  echo 'Run as the normal deployment user, not root.'
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer required"'
python3 scripts/verify_deploy_bundle.py
if [ -e .venv ]; then
  echo 'A .venv already exists; stop and inspect it before continuing.'
  exit 1
fi
if ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
  echo 'Missing Debian venv support. Run the following, then rerun this script:'
  echo 'sudo apt-get update'
  echo 'sudo apt-get install -y python3-venv'
  exit 2
fi
python3 -m venv .venv
.venv/bin/python -m pip install --require-virtualenv -r requirements.txt
.venv/bin/python -m pip check
mkdir -p .cache
.venv/bin/python -m pytest -q --basetemp=.cache/pytest-server-test
.venv/bin/python scripts/run_daily.py --preview
.venv/bin/python scripts/run_daily.py --status
python3 scripts/verify_deploy_bundle.py
echo 'Installation and local checks passed. Start the loopback test page with:'
echo '.venv/bin/python scripts/run_local.py --port 8503'
