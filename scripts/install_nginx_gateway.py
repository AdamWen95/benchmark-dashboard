#!/usr/bin/env python3
"""User-run, one-time installation of the existing dashboard under /benchmark/.

Standard library only. No application imports, secrets, data writes, API calls,
package installation, firewall changes, or scheduled jobs.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UNIT_NAME = "benchmark-dashboard.service"
UNIT = Path("/etc/systemd/system") / UNIT_NAME
DROPIN = Path(str(UNIT) + ".d") / "30-benchmark-gateway.conf"
SITE = Path("/www/server/panel/vhost/nginx/phpfpm_status.conf")
NGINX = "/www/server/nginx/sbin/nginx"
NGINX_CONF = "/www/server/nginx/conf/nginx.conf"
BACKUPS = Path("/var/backups/benchmark-dashboard-gateway")
UNIT_SHA256 = "50685465ea4a9b3ed4fe5bedd0ec00590e18bf7bb3d64fe9f376523b30569bc7"
BEGIN = "    # BEGIN benchmark-dashboard gateway v1"
END = "    # END benchmark-dashboard gateway v1"


class InstallError(Exception):
    pass


def run(args: list[str]) -> str:
    """Never echo captured configuration, environment, or service logs."""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"Command could not finish: {Path(args[0]).name}") from exc
    if result.returncode:
        raise InstallError(f"Command failed: {Path(args[0]).name} {args[1]}")
    return result.stdout.strip()


def location_block() -> str:
    locations = []
    # Both paths use the access phase; no public rewrite-phase redirect.
    for match in ("= /benchmark", "^~ /benchmark/"):
        locations.append("\n".join([
            f"    location {match} {{",
            "        satisfy all;",
            "        allow 127.0.0.1;",
            "        deny all;",
            "        proxy_pass http://127.0.0.1:18503;",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Host $http_host;",
            "        proxy_set_header Upgrade $http_upgrade;",
            '        proxy_set_header Connection "upgrade";',
            "        proxy_set_header X-Real-IP $remote_addr;",
            "        proxy_set_header X-Forwarded-For $remote_addr;",
            "        proxy_set_header X-Forwarded-Proto $scheme;",
            "        proxy_buffering off;",
            "        proxy_cache off;",
            "        proxy_read_timeout 300s;",
            "        proxy_send_timeout 300s;",
            '        add_header X-Benchmark-Gateway "v1" always;',
            "    }",
        ]))
    return "\n" + BEGIN + "\n" + "\n".join(locations) + "\n" + END + "\n"


def patch_site(original: bytes) -> bytes:
    text = original.decode("utf-8")
    # This installer handles only the supported single-server status-site
    # layout; it does not attempt a general-purpose Nginx rewrite.
    if "/benchmark" in text or "benchmark-dashboard gateway" in text:
        raise InstallError("The site already mentions benchmark; no overwrite performed.")
    servers = re.findall(r"(?m)^[ \t]*server[ \t\r\n]*\{", text)
    listens = [value.strip() for value in re.findall(r"(?m)^[ \t]*listen[ \t]+([^;\n]+);", text)]
    names = list(re.finditer(r"(?m)^[ \t]*server_name[ \t]+([^;\n]+);", text))
    if len(servers) != 1 or listens != ["80"] or len(names) != 1 or names[0][1].strip() != "127.0.0.1":
        raise InstallError("The status site layout differs from the inspected single port-80 server.")
    insertion = names[0].end()
    return (text[:insertion] + location_block() + text[insertion:]).encode("utf-8")


def make_dropin(original: bytes) -> bytes:
    if hashlib.sha256(original).hexdigest() != UNIT_SHA256:
        raise InstallError("The installed service differs from the reviewed service; no overwrite performed.")
    text = original.decode("utf-8")
    match = re.search(r"(?m)^ExecStart=((?:[^\n]*\\\n)*[^\n]*)", text)
    if not match:
        raise InstallError("Cannot locate the reviewed service command.")
    command = match[1]
    for before, after in (
        ("--server.address=127.0.0.1", "--server.address=127.0.0.1"),
        ("--server.port=8503", "--server.port=18503"),
        ("--browser.serverPort=8503", "--browser.serverPort=80"),
    ):
        if command.count(before) != 1:
            raise InstallError("Unexpected service command; no changes made.")
        command = command.replace(before, after)
    return ("# benchmark-dashboard gateway v1\n[Service]\nExecStart=\nExecStart="
            + command + " \\\n    --server.baseUrlPath=benchmark\n").encode("utf-8")


def require_regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise InstallError(f"Missing or symbolic-link configuration: {path}")
    for parent in path.parents:
        if parent.is_symlink():
            raise InstallError(f"Symbolic-link configuration directory: {parent}")


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    previous = path.stat() if path.exists() else None
    descriptor, temporary = tempfile.mkstemp(prefix=".benchmark-gateway-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            if previous is not None and hasattr(os, "fchown"):
                os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def check_health(url: str, *, gateway: bool = False) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        request = urllib.request.Request(url, headers={"Host": "127.0.0.1"})
        try:
            with opener.open(request, timeout=2) as response:
                marker = response.headers.get("X-Benchmark-Gateway")
                if response.status == 200 and response.read(16).strip() == b"ok":
                    if not gateway or marker == "v1":
                        return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise InstallError("Local webpage health check failed (no external API was requested).")


def nginx_check() -> None:
    run([NGINX, "-t", "-c", NGINX_CONF])


def nginx_reload() -> None:
    run([NGINX, "-s", "reload", "-c", NGINX_CONF])


def install() -> None:
    require_regular(UNIT)
    require_regular(SITE)
    if UNIT.stat().st_uid != 0 or SITE.stat().st_uid != 0:
        raise InstallError("The service and Nginx site must be root-owned.")
    if DROPIN.exists() or DROPIN.is_symlink() or DROPIN.parent.is_symlink():
        raise InstallError("The gateway override already exists or has an unsafe path; not overwritten.")
    if run(["systemctl", "show", UNIT_NAME, "--property=FragmentPath", "--value"]) != str(UNIT):
        raise InstallError("Unexpected active service file.")
    if run(["systemctl", "show", UNIT_NAME, "--property=DropInPaths", "--value"]):
        raise InstallError("Existing service overrides need review; no changes made.")
    run(["systemctl", "is-active", "--quiet", UNIT_NAME])
    original_site, original_unit = SITE.read_bytes(), UNIT.read_bytes()
    new_site, new_dropin = patch_site(original_site), make_dropin(original_unit)
    nginx_check()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 18503))
    except OSError as exc:
        raise InstallError("127.0.0.1:18503 is occupied; no process was stopped.") from exc

    if BACKUPS.is_symlink():
        raise InstallError("Backup directory is a symbolic link.")
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = BACKUPS / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup.mkdir(mode=0o700)
    shutil.copy2(SITE, backup / SITE.name)
    shutil.copy2(UNIT, backup / UNIT.name)
    print(f"Backup: {backup}", flush=True)
    changed_site = changed_dropin = daemon_changed = nginx_changed = False
    try:
        if SITE.read_bytes() != original_site or UNIT.read_bytes() != original_unit:
            raise InstallError("Configuration changed during preparation; no overwrite performed.")
        DROPIN.parent.mkdir(mode=0o755, exist_ok=True)
        atomic_write(DROPIN, new_dropin, 0o644)
        changed_dropin = True
        atomic_write(SITE, new_site, SITE.stat().st_mode & 0o777)
        changed_site = True
        nginx_check()
        run(["systemd-analyze", "verify", str(UNIT)])
        daemon_changed = True
        run(["systemctl", "daemon-reload"])
        if run(["systemctl", "show", UNIT_NAME, "--property=DropInPaths", "--value"]) != str(DROPIN):
            raise InstallError("Unexpected service overrides after reload.")
        run(["systemctl", "restart", UNIT_NAME])
        check_health("http://127.0.0.1:18503/benchmark/_stcore/health")
        nginx_changed = True
        nginx_reload()
        check_health("http://127.0.0.1/benchmark/_stcore/health", gateway=True)
        run(["systemctl", "is-active", "--quiet", UNIT_NAME])
    except BaseException as exc:
        restore_errors = []
        site_restored = not changed_site
        dropin_restored = not changed_dropin
        try:
            if changed_site:
                if SITE.is_symlink() or SITE.read_bytes() != new_site:
                    raise InstallError("Site changed concurrently; automatic restore stopped.")
                atomic_write(SITE, original_site, (backup / SITE.name).stat().st_mode & 0o777)
            site_restored = True
        except Exception:
            restore_errors.append("site file")
        try:
            if changed_dropin:
                if DROPIN.is_symlink() or DROPIN.read_bytes() != new_dropin:
                    raise InstallError("Override changed concurrently; automatic restore stopped.")
                DROPIN.unlink()
            dropin_restored = True
        except Exception:
            restore_errors.append("service override")
        try:
            if daemon_changed and dropin_restored:
                run(["systemctl", "daemon-reload"])
                run(["systemctl", "restart", UNIT_NAME])
        except Exception:
            restore_errors.append("service reload/restart")
        try:
            if nginx_changed and site_restored:
                nginx_check()
                nginx_reload()
        except Exception:
            restore_errors.append("Nginx reload")
        if restore_errors:
            print(f"Automatic restore incomplete ({', '.join(restore_errors)}). Keep the backup at {backup} and report this message.", file=sys.stderr)
        else:
            print("Installation failed; the previous configuration was restored.", file=sys.stderr)
        raise InstallError(str(exc) if isinstance(exc, InstallError) else "Installation failed.") from exc
    print("Local checks passed. URL: http://127.0.0.1/benchmark/")
    print("Only loopback clients are allowed; review access rules before enabling remote access.")
    print("No firewall changes, API requests, data changes, or daily scheduler changes.")


def main() -> int:
    if sys.platform != "linux" or os.geteuid() != 0:
        print("Run on the specified Linux server with sudo /usr/bin/python3 -I.", file=sys.stderr)
        return 1
    # Lock only this installer; never stop or signal other projects.
    import fcntl
    with open("/run/lock/benchmark-dashboard-gateway.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            install()
        except BlockingIOError:
            print("Another gateway installation is running.", file=sys.stderr)
            return 1
        except (InstallError, OSError, UnicodeError) as exc:
            print(f"Stopped: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
