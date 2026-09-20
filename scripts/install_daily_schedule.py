#!/usr/bin/env python3
"""Install only the approved daily timer; never run collection or AI requests.

Run with sudo /usr/bin/python3 -I on the deployment host. --check performs
read-only preflight checks, including the application's offline --check mode.
Existing units, overrides, masks, and runtime units are deliberately rejected.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path("/opt/benchmark-dashboard")
UNIT_DIR = Path("/etc/systemd/system")
UNIT_SEARCH_DIRS = (
    UNIT_DIR, Path("/run/systemd/system"), Path("/usr/local/lib/systemd/system"),
    Path("/usr/lib/systemd/system"), Path("/lib/systemd/system"),
)
BACKUPS = Path("/var/backups/benchmark-dashboard-daily")
SERVICE = "benchmark-dashboard-daily.service"
TIMER = "benchmark-dashboard-daily.timer"
HASHES = {
    SERVICE: "83b1c1921ee261477296e09317dc1421a9c2cae918416d4d02c4c0ed9c7a0b99",
    TIMER: "996e6ff34f41fc51792fa6bdaf084e7366eb1e0ce0f004b162c1045f0b4485cd",
}


class InstallError(Exception):
    pass


def run(args: list[str], *, cwd: Path | None = None) -> str:
    """Do not echo child output: preflight could have read a credential file."""
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"Command did not finish: {Path(args[0]).name}") from exc
    if result.returncode:
        raise InstallError(f"Command failed: {Path(args[0]).name} {args[1]}")
    return result.stdout.strip()


def require_safe_path(path: Path, *, directory: bool = False) -> None:
    if path.is_symlink() or not (path.is_dir() if directory else path.is_file()):
        raise InstallError(f"Missing or symbolic-link path: {path}")
    if any(parent.is_symlink() for parent in path.parents):
        raise InstallError(f"Symbolic-link parent directory: {path}")


def show(unit: str, prop: str) -> str:
    return run(["systemctl", "show", unit, f"--property={prop}", "--value"])


def ensure_no_conflicts() -> None:
    for name in HASHES:
        for directory in UNIT_SEARCH_DIRS:
            for candidate in (directory / name, directory / (name + ".d")):
                if candidate.exists() or candidate.is_symlink():
                    raise InstallError(f"Existing unit or override requires review: {candidate}")
        # Also detect generators, transient units, and drop-ins in other paths.
        if show(name, "LoadState") != "not-found":
            raise InstallError(f"Existing loaded unit requires review: {name}")
        for prop in ("FragmentPath", "DropInPaths", "UnitFileState"):
            if show(name, prop):
                raise InstallError(f"Existing systemd configuration requires review: {name}")
        if show(name, "ActiveState") not in ("", "inactive"):
            raise InstallError(f"Existing active unit requires review: {name}")


def preflight() -> tuple[dict[str, bytes], dict[str, bytes]]:
    require_safe_path(ROOT, directory=True)
    require_safe_path(ROOT / "data", directory=True)
    require_safe_path(UNIT_DIR, directory=True)
    if UNIT_DIR.stat().st_uid != 0:
        raise InstallError("The systemd target directory must be root-owned.")
    python = ROOT / ".venv/bin/python"
    # Linux venv interpreters normally are symbolic links to the system Python.
    if not python.is_file() or not os.access(python, os.X_OK):
        raise InstallError("The existing deployment Python is missing or not executable.")
    require_safe_path(ROOT / "scripts/run_scheduled_daily.py")
    configs = {}
    for name in ("daily_config.json", "daily_schedule.json"):
        require_safe_path(ROOT / name)
        configs[name] = (ROOT / name).read_bytes()
    sources = {}
    for name, digest in HASHES.items():
        source = ROOT / "deploy" / name
        require_safe_path(source)
        sources[name] = source.read_bytes()
        if hashlib.sha256(sources[name]).hexdigest() != digest:
            raise InstallError(f"Unit differs from the reviewed schedule: {name}")
    ensure_no_conflicts()
    run([
        "runuser", "-u", "benchmark-dashboard", "--", "env", "PYTHONDONTWRITEBYTECODE=1",
        str(python), str(ROOT / "scripts/run_scheduled_daily.py"), "--check",
    ], cwd=ROOT)
    run(["systemd-analyze", "verify", *(str(ROOT / "deploy" / name) for name in HASHES)])
    if any((ROOT / name).read_bytes() != value for name, value in configs.items()):
        raise InstallError("Daily configuration changed during preflight.")
    return sources, configs


def create_unit(path: Path, data: bytes) -> None:
    """Publish a complete unit without replacing a concurrently created file."""
    descriptor, temporary = tempfile.mkstemp(prefix=".benchmark-daily-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def make_backup(sources: dict[str, bytes], configs: dict[str, bytes]) -> Path:
    if BACKUPS.is_symlink() or any(parent.is_symlink() for parent in BACKUPS.parents):
        raise InstallError("Backup path contains a symbolic link.")
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    if BACKUPS.stat().st_uid != 0:
        raise InstallError("Backup directory must be root-owned.")
    backup = BACKUPS / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup.mkdir(mode=0o700)
    for name, contents in {**sources, **configs}.items():
        (backup / name).write_bytes(contents)
        (backup / name).chmod(0o600)
    (backup / "manifest.json").write_text(json.dumps({
        "target_directory": str(UNIT_DIR),
        "previous_units": {name: "absent" for name in sources},
        "proposed_sha256": {name: hashlib.sha256(value).hexdigest() for name, value in sources.items()},
        "schedule": "09:00 Asia/Shanghai; no catch-up; no immediate job execution",
    }, indent=2) + "\n", encoding="utf-8")
    return backup


def install() -> None:
    sources, configs = preflight()
    backup = make_backup(sources, configs)
    print(f"Backup and proposed units: {backup}", flush=True)
    created = []
    activation_attempted = False
    try:
        ensure_no_conflicts()
        if any((ROOT / name).read_bytes() != value for name, value in configs.items()):
            raise InstallError("Daily configuration changed before installation.")
        for name, contents in sources.items():
            create_unit(UNIT_DIR / name, contents)
            created.append(name)
        run(["systemd-analyze", "verify", *(str(UNIT_DIR / name) for name in sources)])
        run(["systemctl", "daemon-reload"])
        for name in sources:
            if show(name, "FragmentPath") != str(UNIT_DIR / name) or show(name, "DropInPaths"):
                raise InstallError(f"Unexpected effective unit or override: {name}")
        activation_attempted = True
        # Starting a non-persistent calendar timer schedules the next occurrence.
        # The service is intentionally never started by this installer.
        run(["systemctl", "enable", "--now", TIMER])
        run(["systemctl", "is-enabled", "--quiet", TIMER])
        run(["systemctl", "is-active", "--quiet", TIMER])
        next_run = show(TIMER, "NextElapseUSecRealtime")
        if not next_run or next_run in ("n/a", "0"):
            raise InstallError("The timer has no next scheduled execution.")
    except BaseException as exc:
        errors = []
        if activation_attempted:
            try:
                run(["systemctl", "disable", "--now", TIMER])
            except Exception:
                errors.append("timer disable/stop")
        for name in reversed(created):
            try:
                target = UNIT_DIR / name
                if target.is_symlink() or target.read_bytes() != sources[name]:
                    raise InstallError("Unit changed concurrently; preserved for review.")
                target.unlink()
            except Exception:
                errors.append(name)
        if created:
            try:
                run(["systemctl", "daemon-reload"])
            except Exception:
                errors.append("systemd daemon reload")
        state = "rollback incomplete: " + ", ".join(errors) if errors else "installation rolled back"
        (backup / "result.txt").write_text(state + "\n", encoding="utf-8")
        print(f"Installation failed; {state}. Evidence: {backup}", file=sys.stderr)
        raise InstallError(str(exc) if isinstance(exc, InstallError) else "Installation failed.") from exc
    (backup / "result.txt").write_text(f"timer enabled and active\nnext_run={next_run}\n", encoding="utf-8")
    print(f"Timer enabled and active. Next scheduled execution: {next_run}")
    print("No collection or AI command was run. No web unit, database, firewall, or Nginx changes.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only offline preflight; do not install or enable units")
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() != 0:
        print("Run on the deployment Linux host with sudo /usr/bin/python3 -I.", file=sys.stderr)
        return 1
    try:
        if args.check:
            preflight()
            print("Read-only preflight passed; timer not installed or started.")
            return 0
        import fcntl
        with open("/run/lock/benchmark-dashboard-daily-install.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise InstallError("Another daily schedule installer is running.") from exc
            install()
    except (InstallError, OSError, UnicodeError) as exc:
        # OSError messages contain paths, never captured subprocess output.
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
