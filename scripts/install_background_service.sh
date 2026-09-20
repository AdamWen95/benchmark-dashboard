#!/usr/bin/env bash
# Run locally as benchmark-dashboard; no data API, model request, timer, or firewall changes.
set -euo pipefail
umask 077
ROOT=/opt/benchmark-dashboard
UNIT=benchmark-dashboard.service
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/../deploy/$UNIT"
TARGET="/etc/systemd/system/$UNIT"
URL=http://127.0.0.1:8503
fail() { printf '%s\n' "$1" >&2; exit 1; }
if [ "$(id -u)" -eq 0 ] || [ "$(id -un)" != benchmark-dashboard ]; then
  fail 'Run this script as benchmark-dashboard, not root or another user.'
fi
[ "$(id -gn)" = benchmark-dashboard ] || fail 'The primary group must be benchmark-dashboard.'
for tool in sudo systemctl systemd-analyze install cmp stat; do
  command -v "$tool" >/dev/null 2>&1 || fail "Required command missing: $tool"
done
[ -d /run/systemd/system ] || fail 'A running systemd system manager is required.'
[ -f "$SOURCE" ] && [ -r "$SOURCE" ] && [ ! -L "$SOURCE" ] || fail 'Missing or unsafe unit file in the project deploy directory.'
[ -d "$ROOT" ] && [ ! -L "$ROOT" ] || fail 'The fixed project directory is missing or is a symbolic link.'
[ -d "$ROOT/data" ] && [ ! -L "$ROOT/data" ] || fail 'The project data directory is missing or is a symbolic link.'
cd -- "$ROOT"
[ -x .venv/bin/python ] || fail 'The existing project Python environment is missing.'
load_status=0
load_state=$(systemctl show "$UNIT" --property=LoadState --value) || load_status=$?
case "$load_state" in
  not-found) fragment=; dropins= ;;
  loaded)
    [ "$load_status" -eq 0 ] || fail 'Cannot inspect the existing unit.'
    fragment=$(systemctl show "$UNIT" --property=FragmentPath --value) || fail 'Cannot inspect the existing unit.'
    dropins=$(systemctl show "$UNIT" --property=DropInPaths --value) || fail 'Cannot inspect unit overrides.'
    ;;
  *) fail 'The unit is masked, invalid, or cannot be inspected; no changes made.' ;;
esac
[ -z "$fragment" ] || [ "$fragment" = "$TARGET" ] || fail 'The unit is loaded from an unexpected path; inspect it manually.'
[ -z "$dropins" ] || fail 'Unit drop-ins exist; inspect them manually before continuing.'
check_target() {
  [ ! -L "$TARGET" ] || fail 'The destination unit is a symbolic link; no changes made.'
  if [ -e "$TARGET" ]; then
    [ -f "$TARGET" ] && cmp -s -- "$SOURCE" "$TARGET" || fail 'A different destination unit exists; it will not be overwritten.'
    [ "$(stat -c '%u:%g:%a' -- "$TARGET")" = 0:0:644 ] || fail 'The existing unit must already be owned by root:root with mode 0644.'
  fi
}
check_target
.venv/bin/python - <<'PY'
import json
from pathlib import Path
try:
    value = json.loads(Path('daily_config.json').read_text(encoding='utf-8-sig'))
    valid = isinstance(value, dict) and value.get('enabled') is False
except Exception:
    valid = False
if not valid:
    raise SystemExit('daily_config.json must contain enabled: false; no configuration was changed.')
PY
mkdir -p .cache
PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 .venv/bin/python -m pip check
.venv/bin/python -m pytest -q --tb=short --maxfail=3 --basetemp=.cache/pytest-service-setup
.venv/bin/python scripts/run_daily.py --preview
.venv/bin/python scripts/run_daily.py --status
if ! systemctl is-active --quiet "$UNIT"; then
  .venv/bin/python - <<'PY'
import socket
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 8503))
except OSError:
    raise SystemExit('Cannot bind 127.0.0.1:8503. Check the host address and port locally; no process was stopped.') from None
PY
fi
systemd-analyze verify "$SOURCE"
printf '%s\n' 'Local checks passed. sudo may request your password on this machine.'
sudo -v
check_target
if [ ! -e "$TARGET" ]; then
  sudo install -o root -g root -m 0644 -- "$SOURCE" "$TARGET"
fi
check_target
sudo systemctl daemon-reload
fragment=$(systemctl show "$UNIT" --property=FragmentPath --value) || fail 'Cannot verify the installed unit.'
dropins=$(systemctl show "$UNIT" --property=DropInPaths --value) || fail 'Cannot inspect installed unit overrides.'
[ "$fragment" = "$TARGET" ] || fail 'The installed unit resolved to an unexpected path; service was not started.'
[ -z "$dropins" ] || fail 'Overrides were discovered after installation; service was not started.'
service_failed() {
  sudo systemctl stop "$UNIT" >/dev/null 2>&1 || true
  printf '%s\n' 'Service setup failed. Only benchmark-dashboard.service was stopped; its unit file was retained.' >&2
  printf '%s\n' 'Inspect locally: sudo journalctl -u benchmark-dashboard.service -n 80 --no-pager' >&2
  exit 1
}
sudo systemctl start "$UNIT" || service_failed
if ! .venv/bin/python - <<'PY'
import time
import signal
import urllib.error
import urllib.request
class LocalHealthOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None
def expired(signum, frame):
    raise SystemExit(1)
signal.signal(signal.SIGALRM, expired)
signal.alarm(30)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalHealthOnly())
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        with opener.open('http://127.0.0.1:8503/_stcore/health', timeout=min(2, remaining)) as response:
            if response.status == 200 and response.read(16).strip() == b'ok':
                raise SystemExit(0)
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(min(0.5, max(0, deadline - time.monotonic())))
raise SystemExit(1)
PY
then
  service_failed
fi
systemctl is-active --quiet "$UNIT" || service_failed
sudo systemctl enable "$UNIT" || service_failed
systemctl show "$UNIT" --property=ActiveState --property=SubState --property=UnitFileState --property=FragmentPath --property=User
printf 'Local URL: %s\n' "$URL"
