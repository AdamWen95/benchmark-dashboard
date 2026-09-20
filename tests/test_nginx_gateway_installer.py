"""Isolated gateway installation checks: synthetic files and fake host tools only."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_nginx_gateway.py"
SPEC = importlib.util.spec_from_file_location("gateway_installer_under_test", SCRIPT)
assert SPEC and SPEC.loader
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)

SITE_TEXT = """# Synthetic pre-existing status site.
server {
    listen 80;
    server_name 127.0.0.1;
    location /nginx_status {
        stub_status on;
        allow 127.0.0.1;
        deny all;
    }
    location /existing-app/ {
        proxy_pass http://127.0.0.1:19001;
    }
}
"""
UNIT_TEXT = """[Unit]
Description=Synthetic dashboard
[Service]
User=synthetic
Group=synthetic
ExecStart=/synthetic/python -m streamlit run /synthetic/app.py \\
    --server.address=127.0.0.1 --server.port=8503 \\
    --browser.serverAddress=127.0.0.1 --browser.serverPort=8503 \\
    --server.headless=true --server.fileWatcherType=none \\
    --server.enableCORS=true --server.enableXsrfProtection=true \\
    --server.enableStaticServing=false --browser.gatherUsageStats=false \\
    --client.showErrorDetails=none --client.toolbarMode=viewer
NoNewPrivileges=true
ProtectSystem=full
ReadOnlyPaths=/synthetic/data
InaccessiblePaths=-/synthetic/.env
"""


@pytest.fixture(autouse=True)
def prohibit_host_interactions(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A real host command or network operation was attempted")

    monkeypatch.setattr(gateway.subprocess, "run", forbidden)
    monkeypatch.setattr(gateway.socket, "socket", forbidden)
    monkeypatch.setattr(gateway.urllib.request, "build_opener", forbidden)


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_patch_preserves_every_original_byte_and_existing_routes(line_ending):
    original = SITE_TEXT.replace("\n", line_ending).encode()
    patched = gateway.patch_site(original)
    block = gateway.location_block().encode()
    assert patched.count(block) == 1
    assert patched.replace(block, b"", 1) == original
    assert b"location /nginx_status" in patched
    assert b"location /existing-app/" in patched
    assert b"proxy_pass http://127.0.0.1:19001;" in patched


@pytest.mark.parametrize(
    "original",
    [
        SITE_TEXT.replace("listen 80;", "listen 8080;"),
        SITE_TEXT.replace("listen 80;", "listen 80 default_server;"),
        SITE_TEXT.replace("listen 80;", "listen 80;\n    listen [::]:80;"),
        SITE_TEXT.replace("server_name 127.0.0.1;", "server_name existing.example;"),
        SITE_TEXT.replace("server_name 127.0.0.1;", "server_name 127.0.0.1 other.example;"),
        SITE_TEXT.replace("server_name 127.0.0.1;", "server_name 127.0.0.1;\n    server_name other.example;"),
        SITE_TEXT + "\nserver {\n    listen 81;\n}\n",
        SITE_TEXT.replace("server {", "# server {"),
    ],
)
def test_patch_refuses_unreviewed_server_layout(original):
    with pytest.raises(gateway.InstallError):
        gateway.patch_site(original.encode())


@pytest.mark.parametrize("mention", ["/benchmark", "benchmark-dashboard gateway"])
def test_patch_refuses_existing_gateway_even_in_comment(mention):
    with pytest.raises(gateway.InstallError, match="already mentions"):
        gateway.patch_site((SITE_TEXT + f"# {mention}\n").encode())


def test_patch_refuses_second_installation():
    with pytest.raises(gateway.InstallError, match="already mentions"):
        gateway.patch_site(gateway.patch_site(SITE_TEXT.encode()))


@pytest.mark.parametrize("listen", ["listen 80;", "listen   80   ;", "listen\t80\t;"])
def test_patch_accepts_inspected_style_with_separate_server_brace(listen):
    original = SITE_TEXT.replace("server {", "server\n{").replace("listen 80;", listen).encode()
    block = gateway.location_block().encode()
    assert gateway.patch_site(original).replace(block, b"", 1) == original


def test_gateway_paths_both_enforce_narrow_access_and_websocket_support():
    block = gateway.location_block()
    assert "location = /benchmark {" in block
    assert "location ^~ /benchmark/ {" in block
    assert block.count("satisfy all;") == 2
    assert block.count("allow 127.0.0.1;") == 2
    assert block.count("allow ") == 2
    assert block.count("deny all;") == 2
    assert block.count("proxy_set_header Upgrade $http_upgrade;") == 2
    assert block.count("proxy_set_header X-Forwarded-For $remote_addr;") == 2
    assert "return 301" not in block
    assert "allow 10.0.0.0/8" not in block


def test_health_probe_selects_the_supported_nginx_virtual_host(monkeypatch):
    class HealthyResponse:
        status = 200
        headers = {"X-Benchmark-Gateway": "v1"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b"ok"

    def open_local(request, *, timeout):
        assert request.full_url == "http://127.0.0.1/benchmark/_stcore/health"
        assert request.get_header("Host") == "127.0.0.1"
        assert timeout == 2
        return HealthyResponse()

    monkeypatch.setattr(gateway.urllib.request, "build_opener",
                        lambda *handlers: SimpleNamespace(open=open_local))
    gateway.check_health("http://127.0.0.1/benchmark/_stcore/health", gateway=True)


@pytest.fixture
def reviewed_unit(monkeypatch):
    original = UNIT_TEXT.encode()
    monkeypatch.setattr(gateway, "UNIT_SHA256", hashlib.sha256(original).hexdigest())
    return original


def test_dropin_preserves_security_flags_and_original_unit(reviewed_unit):
    result = gateway.make_dropin(reviewed_unit).decode()
    assert "ExecStart=\nExecStart=" in result
    for flag in (
        "--server.address=127.0.0.1", "--server.port=18503",
        "--browser.serverAddress=127.0.0.1", "--browser.serverPort=80",
        "--server.baseUrlPath=benchmark", "--server.enableCORS=true",
        "--server.enableXsrfProtection=true", "--server.enableStaticServing=false",
        "--browser.gatherUsageStats=false", "--server.fileWatcherType=none",
        "--client.showErrorDetails=none", "--client.toolbarMode=viewer",
    ):
        assert flag in result
    assert result.count("--server.address=127.0.0.1") == 1
    assert "--server.port=8503" not in result
    # The override only replaces ExecStart, leaving the original sandbox intact.
    assert result.count("[Service]") == 1
    assert "ReadOnlyPaths=" not in result
    assert reviewed_unit == UNIT_TEXT.encode()


def test_dropin_rejects_unreviewed_unit_hash(reviewed_unit):
    with pytest.raises(gateway.InstallError, match="differs from the reviewed"):
        gateway.make_dropin(reviewed_unit + b"# locally modified\n")


@pytest.mark.parametrize("old", ["--server.address=127.0.0.1", "--server.port=8503", "--browser.serverPort=8503"])
def test_dropin_refuses_missing_expected_argument_even_if_hash_matches(monkeypatch, old):
    unit = UNIT_TEXT.replace(old, "--unexpected=true").encode()
    monkeypatch.setattr(gateway, "UNIT_SHA256", hashlib.sha256(unit).hexdigest())
    with pytest.raises(gateway.InstallError, match="Unexpected service command"):
        gateway.make_dropin(unit)


@pytest.fixture
def isolated_host(tmp_path, monkeypatch, reviewed_unit):
    unit = tmp_path / "systemd" / gateway.UNIT_NAME
    site = tmp_path / "nginx" / "synthetic-status.conf"
    dropin = Path(str(unit) + ".d") / "30-benchmark-gateway.conf"
    backups = tmp_path / "backups"
    unit.parent.mkdir()
    site.parent.mkdir()
    unit.write_bytes(reviewed_unit)
    site.write_bytes(SITE_TEXT.encode())
    for name, value in (("UNIT", unit), ("SITE", site), ("DROPIN", dropin), ("BACKUPS", backups)):
        monkeypatch.setattr(gateway, name, value)

    # Preserve real temporary-file metadata but emulate root ownership on Windows.
    original_stat = Path.stat

    class RootOwnedStat:
        st_uid = 0

        def __init__(self, result):
            self.result = result

        def __getattr__(self, name):
            return getattr(self.result, name)

    def root_owned_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        return RootOwnedStat(result) if path in (unit, site) else result

    monkeypatch.setattr(Path, "stat", root_owned_stat)
    monkeypatch.setattr(gateway.os, "fchmod", lambda descriptor, mode: None, raising=False)
    monkeypatch.setattr(gateway.os, "fchown", lambda descriptor, uid, gid: None, raising=False)
    host = SimpleNamespace(
        unit=unit, site=site, dropin=dropin, backups=backups,
        commands=[], health=[], binds=[], fail_phase=None, failed=False,
        run_counts={}, existing_overrides="", occupied=False,
    )

    def fail_once(phase):
        if phase == host.fail_phase and not host.failed:
            host.failed = True
            raise gateway.InstallError(f"Synthetic failure: {phase}")

    def fake_run(args):
        host.commands.append(tuple(args))
        command = tuple(args[:2])
        host.run_counts[command] = host.run_counts.get(command, 0) + 1
        if args[:2] == ["systemctl", "show"]:
            if "--property=FragmentPath" in args:
                return str(unit)
            return host.existing_overrides or (str(dropin) if dropin.exists() else "")
        if args[:2] == [gateway.NGINX, "-t"] and host.run_counts[command] == 2:
            fail_once("nginx_validation")
        if args[:2] == ["systemd-analyze", "verify"]:
            fail_once("systemd_validation")
        if args[:2] == ["systemctl", "daemon-reload"]:
            fail_once("daemon_reload")
        if args[:2] == ["systemctl", "restart"]:
            fail_once("service_restart")
        if args[:3] == [gateway.NGINX, "-s", "reload"]:
            fail_once("nginx_reload")
        if args[:2] == ["systemctl", "is-active"] and host.run_counts[command] == 2:
            fail_once("final_active")
        return ""

    def fake_health(url, *, gateway=False):
        host.health.append((url, gateway))
        fail_once("gateway_health" if gateway else "upstream_health")

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def bind(self, address):
            host.binds.append(address)
            if host.occupied:
                raise OSError("Synthetic occupied port")

    monkeypatch.setattr(gateway, "run", fake_run)
    monkeypatch.setattr(gateway, "check_health", fake_health)
    monkeypatch.setattr(gateway.socket, "socket", lambda *args, **kwargs: FakeSocket())
    return host


def assert_original_files_and_backups(host):
    assert host.unit.read_bytes() == UNIT_TEXT.encode()
    assert host.site.read_bytes() == SITE_TEXT.encode()
    assert not host.dropin.exists()
    backup_folders = list(host.backups.iterdir())
    assert len(backup_folders) == 1
    assert (backup_folders[0] / host.site.name).read_bytes() == SITE_TEXT.encode()
    assert (backup_folders[0] / host.unit.name).read_bytes() == UNIT_TEXT.encode()


def test_successful_install_uses_loopback_health_and_retains_exact_backup(isolated_host):
    host = isolated_host
    gateway.install()
    assert host.site.read_bytes() == gateway.patch_site(SITE_TEXT.encode())
    assert host.dropin.read_bytes() == gateway.make_dropin(UNIT_TEXT.encode())
    assert host.unit.read_bytes() == UNIT_TEXT.encode()
    assert host.binds == [("127.0.0.1", 18503)]
    assert host.health == [
        ("http://127.0.0.1:18503/benchmark/_stcore/health", False),
        ("http://127.0.0.1/benchmark/_stcore/health", True),
    ]
    backup = next(host.backups.iterdir())
    assert (backup / host.site.name).read_bytes() == SITE_TEXT.encode()
    assert (backup / host.unit.name).read_bytes() == UNIT_TEXT.encode()


@pytest.mark.parametrize("phase", [
    "nginx_validation", "systemd_validation", "daemon_reload", "service_restart",
    "upstream_health", "nginx_reload", "gateway_health", "final_active",
])
def test_failed_install_restores_original_files_and_service_state(isolated_host, capsys, phase):
    host = isolated_host
    host.fail_phase = phase
    with pytest.raises(gateway.InstallError, match=f"Synthetic failure: {phase}"):
        gateway.install()
    assert_original_files_and_backups(host)
    assert "previous configuration was restored" in capsys.readouterr().err
    if phase in {"daemon_reload", "service_restart", "upstream_health", "nginx_reload", "gateway_health", "final_active"}:
        assert host.run_counts[("systemctl", "daemon-reload")] == 2
        assert ("systemctl", "restart", gateway.UNIT_NAME) in host.commands
    if phase in {"nginx_reload", "gateway_health", "final_active"}:
        assert host.run_counts[(gateway.NGINX, "-s")] == 2


def test_occupied_loopback_port_causes_no_config_changes(isolated_host):
    host = isolated_host
    host.occupied = True
    with pytest.raises(gateway.InstallError, match="occupied"):
        gateway.install()
    assert host.site.read_bytes() == SITE_TEXT.encode()
    assert host.unit.read_bytes() == UNIT_TEXT.encode()
    assert not host.dropin.exists()
    assert not host.backups.exists()
    assert not any(command[:2] == ("systemctl", "restart") for command in host.commands)


def test_existing_override_is_not_overwritten(isolated_host):
    host = isolated_host
    host.dropin.parent.mkdir()
    host.dropin.write_bytes(b"# Existing operator configuration\n")
    with pytest.raises(gateway.InstallError, match="override already exists"):
        gateway.install()
    assert host.dropin.read_bytes() == b"# Existing operator configuration\n"
    assert host.site.read_bytes() == SITE_TEXT.encode()
    assert not host.backups.exists()
    assert host.commands == []


def test_other_service_overrides_require_review(isolated_host):
    host = isolated_host
    host.existing_overrides = "/synthetic/other.conf"
    with pytest.raises(gateway.InstallError, match="Existing service overrides"):
        gateway.install()
    assert not host.backups.exists()
    assert not host.dropin.exists()
    assert host.site.read_bytes() == SITE_TEXT.encode()


def test_second_install_does_not_rewrite_working_configuration(isolated_host):
    host = isolated_host
    gateway.install()
    site, override = host.site.read_bytes(), host.dropin.read_bytes()
    commands = list(host.commands)
    with pytest.raises(gateway.InstallError, match="override already exists"):
        gateway.install()
    assert host.site.read_bytes() == site
    assert host.dropin.read_bytes() == override
    assert host.commands == commands
    assert len(list(host.backups.iterdir())) == 1


def test_concurrent_site_change_is_not_destroyed_by_rollback(isolated_host, monkeypatch, capsys):
    host = isolated_host
    concurrent = b"# Synthetic concurrent administrator edit\n"

    error = gateway.InstallError

    def changed_site(url, *, gateway=False):
        host.site.write_bytes(concurrent)
        raise error("Synthetic concurrent failure")

    monkeypatch.setattr(gateway, "check_health", changed_site)
    with pytest.raises(gateway.InstallError, match="Synthetic concurrent failure"):
        gateway.install()
    assert host.site.read_bytes() == concurrent
    assert "Automatic restore incomplete" in capsys.readouterr().err
    backup = next(host.backups.iterdir())
    assert (backup / host.site.name).read_bytes() == SITE_TEXT.encode()


def test_failed_service_restart_during_rollback_still_restores_nginx(isolated_host, monkeypatch, capsys):
    host = isolated_host
    host.fail_phase = "gateway_health"
    fake_run = gateway.run

    def fail_restoring_service(args):
        result = fake_run(args)
        if args[:2] == ["systemctl", "restart"] and host.run_counts[tuple(args[:2])] == 2:
            raise gateway.InstallError("Synthetic rollback service failure")
        return result

    monkeypatch.setattr(gateway, "run", fail_restoring_service)
    with pytest.raises(gateway.InstallError, match="Synthetic failure: gateway_health"):
        gateway.install()
    assert_original_files_and_backups(host)
    assert host.run_counts[(gateway.NGINX, "-s")] == 2
    stderr = capsys.readouterr().err
    assert "Automatic restore incomplete" in stderr
    assert "service reload/restart" in stderr


def test_keyboard_interrupt_restores_original_configuration(isolated_host, monkeypatch, capsys):
    def interrupted_health(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(gateway, "check_health", interrupted_health)
    with pytest.raises(gateway.InstallError, match="Installation failed"):
        gateway.install()
    assert_original_files_and_backups(isolated_host)
    assert "previous configuration was restored" in capsys.readouterr().err


@pytest.mark.parametrize("failed_target", ["site", "dropin"])
def test_initial_file_write_failure_leaves_existing_configuration_intact(isolated_host, monkeypatch, failed_target):
    host = isolated_host
    real_write = gateway.atomic_write
    target = getattr(host, failed_target)

    def failed_write(path, data, mode):
        if path == target:
            raise OSError("Synthetic file write failure")
        real_write(path, data, mode)

    monkeypatch.setattr(gateway, "atomic_write", failed_write)
    with pytest.raises(gateway.InstallError, match="Installation failed"):
        gateway.install()
    assert_original_files_and_backups(host)
    assert not any(command[:2] == ("systemctl", "restart") for command in host.commands)


def test_command_failure_does_not_echo_command_output(monkeypatch, capsys):
    def rejected_command(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="SYNTHETIC_PRIVATE_OUTPUT", stderr="SYNTHETIC_PRIVATE_ERROR")

    monkeypatch.setattr(gateway.subprocess, "run", rejected_command)
    with pytest.raises(gateway.InstallError, match="Command failed: nginx -t") as failure:
        gateway.run(["/synthetic/nginx", "-t"])
    assert "PRIVATE" not in str(failure.value)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
