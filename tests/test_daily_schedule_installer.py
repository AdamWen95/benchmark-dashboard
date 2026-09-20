"""Isolated scheduler installation tests: no real host commands or network."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("daily_installer_under_test", PROJECT / "scripts/install_daily_schedule.py")
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.fixture(autouse=True)
def prevent_real_commands(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A real host command was attempted")
    monkeypatch.setattr(installer.subprocess, "run", forbidden)


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path / "project"
    units = tmp_path / "systemd"
    runtime = tmp_path / "runtime-systemd"
    backups = tmp_path / "backups"
    for directory in (root / "deploy", root / "scripts", root / ".venv/bin", root / "data", units, runtime):
        directory.mkdir(parents=True)
    for name in installer.HASHES:
        (root / "deploy" / name).write_bytes((PROJECT / "deploy" / name).read_bytes())
    (root / ".venv/bin/python").write_text("synthetic interpreter", encoding="utf-8")
    (root / ".venv/bin/python").chmod(0o755)
    (root / "scripts/run_scheduled_daily.py").write_text("# synthetic runner", encoding="utf-8")
    (root / "daily_config.json").write_text('{"enabled": true}', encoding="utf-8")
    (root / "daily_schedule.json").write_text('{"time": "09:00"}', encoding="utf-8")
    (root / ".env").write_text("SYNTHETIC_SECRET=must-not-be-copied", encoding="utf-8")
    (root / "data/historical.db").write_bytes(b"historical fixture")
    for key, value in (("ROOT", root), ("UNIT_DIR", units), ("UNIT_SEARCH_DIRS", (units, runtime)), ("BACKUPS", backups)):
        monkeypatch.setattr(installer, key, value)
    state = SimpleNamespace(root=root, units=units, runtime=runtime, backups=backups, commands=[], properties={}, fail=None, failed=False,
                            synthetic_owners={units: 0, backups: 0})

    # These two directories represent root-managed server locations. Emulate
    # their ownership without requiring tests to run as root on Linux; retain
    # all other real metadata, including the synthetic interpreter's mode.
    original_stat = Path.stat

    class SyntheticOwnershipStat:
        def __init__(self, result, uid):
            self.result = result
            self.st_uid = uid

        def __getattr__(self, name):
            return getattr(self.result, name)

    def synthetic_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path in state.synthetic_owners:
            return SyntheticOwnershipStat(result, state.synthetic_owners[path])
        return result

    monkeypatch.setattr(Path, "stat", synthetic_stat)

    def fake_run(args, *, cwd=None):
        state.commands.append((args, cwd))
        if state.fail and state.fail(args) and not state.failed:
            state.failed = True
            raise installer.InstallError("Synthetic command failure")
        if args[:2] == ["systemctl", "show"]:
            name, prop = args[2], args[3].removeprefix("--property=")
            if (name, prop) in state.properties:
                return state.properties[(name, prop)]
            if prop == "LoadState":
                return "loaded" if (units / name).exists() else "not-found"
            if prop == "FragmentPath":
                return str(units / name) if (units / name).exists() else ""
            if prop == "ActiveState":
                return "inactive"
            if prop == "NextElapseUSecRealtime":
                return "Mon 2026-09-21 09:00:00 CST"
            return ""
        return ""

    monkeypatch.setattr(installer, "run", fake_run)
    return state


@pytest.mark.parametrize("directory", ["units", "backups"])
def test_nonroot_owned_target_or_backup_is_refused(host, directory):
    host.synthetic_owners[getattr(host, directory)] = 1000
    with pytest.raises(installer.InstallError, match="must be root-owned"):
        installer.install()
    assert not list(host.units.iterdir())
    assert not any(cmd[:2] in (["systemctl", "enable"], ["systemctl", "daemon-reload"])
                   for cmd, _ in host.commands)
    assert (host.root / "data/historical.db").read_bytes() == b"historical fixture"


def test_install_starts_only_timer_and_preserves_business_data(host):
    installer.install()
    commands = [cmd for cmd, _ in host.commands]
    assert ["systemctl", "enable", "--now", installer.TIMER] in commands
    assert not any("--execute" in cmd for cmd in commands)
    assert not any(cmd[:2] in (["systemctl", "start"], ["systemctl", "restart"]) for cmd in commands)
    assert (host.root / "data/historical.db").read_bytes() == b"historical fixture"
    backup = next(host.backups.iterdir())
    assert (backup / "daily_config.json").read_bytes() == (host.root / "daily_config.json").read_bytes()
    assert (backup / "daily_schedule.json").read_bytes() == (host.root / "daily_schedule.json").read_bytes()
    assert not (backup / ".env").exists()
    assert "absent" in (backup / "manifest.json").read_text()
    assert "timer enabled and active" in (backup / "result.txt").read_text()


def test_check_is_read_only_and_runs_preflight_as_service_user(host, monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer.os, "geteuid", lambda: 0, raising=False)
    assert installer.main(["--check"]) == 0
    assert not host.backups.exists()
    assert not list(host.units.iterdir())
    commands = [cmd for cmd, _ in host.commands]
    preflight = next(cmd for cmd in commands if cmd[0] == "runuser")
    assert preflight[1:4] == ["-u", "benchmark-dashboard", "--"]
    assert "PYTHONDONTWRITEBYTECODE=1" in preflight
    assert preflight[-1] == "--check"
    assert not any(cmd[:2] in (["systemctl", "enable"], ["systemctl", "daemon-reload"]) for cmd in commands)


@pytest.mark.parametrize("name", [installer.SERVICE, installer.TIMER])
@pytest.mark.parametrize("where", ["units", "runtime"])
@pytest.mark.parametrize("kind", ["unit", "dropin"])
def test_existing_unit_or_override_refused_without_overwrite(host, name, where, kind):
    path = getattr(host, where) / (name + (".d" if kind == "dropin" else ""))
    if kind == "dropin":
        path.mkdir()
    else:
        path.write_bytes(b"preexisting independent configuration")
    with pytest.raises(installer.InstallError, match="Existing unit or override"):
        installer.install()
    assert path.exists()
    assert not host.backups.exists()
    assert not any(cmd[:2] == ["systemctl", "disable"] for cmd, _ in host.commands)


@pytest.mark.parametrize("prop,value", [("LoadState", "loaded"), ("DropInPaths", "/other/override.conf"), ("UnitFileState", "enabled"), ("ActiveState", "active"), ("FragmentPath", "/generated/service")])
def test_loaded_and_generated_units_refused(host, prop, value):
    host.properties[(installer.SERVICE, prop)] = value
    with pytest.raises(installer.InstallError, match="Existing"):
        installer.install()
    assert not host.backups.exists()


def test_changed_source_refused(host):
    path = host.root / "deploy" / installer.SERVICE
    path.write_bytes(path.read_bytes().replace(b"User=benchmark-dashboard", b"User=root"))
    with pytest.raises(installer.InstallError, match="reviewed schedule"):
        installer.install()
    assert not host.backups.exists()


@pytest.mark.parametrize("failure", ["runuser", "systemd-analyze"])
def test_preflight_failure_does_not_mutate_systemd(host, failure):
    host.fail = lambda cmd: cmd[0] == failure
    with pytest.raises(installer.InstallError):
        installer.install()
    assert not host.backups.exists()
    assert not list(host.units.iterdir())


@pytest.mark.parametrize("stage", ["installed-verification", "daemon-reload", "enable", "is-enabled", "is-active", "next-run"])
def test_install_failure_rolls_back_only_new_units(host, stage):
    if stage == "installed-verification":
        host.fail = lambda cmd: cmd[0] == "systemd-analyze" and str(host.units) in cmd[2]
    elif stage == "next-run":
        host.properties[(installer.TIMER, "NextElapseUSecRealtime")] = "n/a"
    else:
        host.fail = lambda cmd: cmd[:2] == ["systemctl", stage]
    with pytest.raises(installer.InstallError):
        installer.install()
    assert not list(host.units.iterdir())
    backup = next(host.backups.iterdir())
    assert "installation rolled back" in (backup / "result.txt").read_text()
    assert (host.root / "data/historical.db").read_bytes() == b"historical fixture"
    if stage in ("enable", "is-enabled", "is-active", "next-run"):
        assert any(cmd == ["systemctl", "disable", "--now", installer.TIMER] for cmd, _ in host.commands)
    assert not any("benchmark-dashboard.service" in cmd for cmd, _ in host.commands)


def test_concurrent_unit_creation_is_preserved_and_first_unit_removed(host, monkeypatch):
    original_create = installer.create_unit
    def concurrent_create(path, contents):
        if path.name == installer.TIMER:
            path.write_bytes(b"concurrent external timer")
        original_create(path, contents)
    monkeypatch.setattr(installer, "create_unit", concurrent_create)
    with pytest.raises(installer.InstallError):
        installer.install()
    assert not (host.units / installer.SERVICE).exists()
    assert (host.units / installer.TIMER).read_bytes() == b"concurrent external timer"


def test_concurrent_change_is_not_removed_during_rollback(host, monkeypatch):
    original_run = installer.run
    def change_on_enable(args, *, cwd=None):
        if args[:2] == ["systemctl", "enable"]:
            (host.units / installer.SERVICE).write_bytes(b"concurrent change")
            raise installer.InstallError("Synthetic failure after concurrent edit")
        return original_run(args, cwd=cwd)
    monkeypatch.setattr(installer, "run", change_on_enable)
    with pytest.raises(installer.InstallError):
        installer.install()
    assert (host.units / installer.SERVICE).read_bytes() == b"concurrent change"
    assert "rollback incomplete" in (next(host.backups.iterdir()) / "result.txt").read_text()


def test_reviewed_units_enforce_schedule_and_sandbox():
    for name, digest in installer.HASHES.items():
        assert hashlib.sha256((PROJECT / "deploy" / name).read_bytes()).hexdigest() == digest
    service = (PROJECT / "deploy" / installer.SERVICE).read_text()
    timer = (PROJECT / "deploy" / installer.TIMER).read_text()
    assert "User=benchmark-dashboard\n" in service
    assert "Restart=no\n" in service
    assert "ProtectSystem=strict\n" in service
    assert "ReadWritePaths=/opt/benchmark-dashboard/data\n" in service
    assert "EnvironmentFile=" not in service
    assert "09:00:00 Asia/Shanghai" in timer
    assert "Persistent=false\n" in timer
    assert "RandomizedDelaySec=0\n" in timer
    assert "OnBootSec=" not in timer


def test_child_failure_output_is_never_echoed(monkeypatch, capsys):
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="synthetic-secret", stderr="synthetic-secret"))
    with pytest.raises(installer.InstallError) as exc:
        installer.run(["runuser", "-u", "benchmark-dashboard"])
    assert "synthetic-secret" not in str(exc.value)
    assert "synthetic-secret" not in capsys.readouterr().out
