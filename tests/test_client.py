"""Boundary tests for credential handling and remote API failures."""

import logging

import pytest
import requests

from benchmark_dashboard.client import API_URL, SOURCE, SourceError, fetch_models
from benchmark_dashboard.config import SafeError, load_api_key


TEST_KEY = "test-only-credential-do-not-use"


class Response:
    def __init__(self, status=200, payload=None, error=None):
        self.status_code = status
        self.payload = {"data": [{"id": "model-1"}]} if payload is None else payload
        self.error = error

    @property
    def text(self):
        raise AssertionError("Response text must never be read for error reporting.")

    @property
    def headers(self):
        raise AssertionError("Response headers must never be read for error reporting.")

    def json(self):
        if self.error:
            raise self.error
        return self.payload


class Session:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def test_success_uses_expected_header_timeouts_and_disallows_redirects():
    session = Session(Response())
    payload, status, attempts = fetch_models(TEST_KEY, session=session)
    assert (status, attempts) == (200, 1)
    assert payload == {"data": [{"id": "model-1"}]}
    assert SOURCE == "artificial_analysis"
    assert session.calls == [(API_URL, {
        "headers": {"x-api-key": TEST_KEY, "Accept": "application/json"},
        "timeout": (10, 45),
        "allow_redirects": False,
    })]


@pytest.mark.parametrize("status,code", [
    (401, "authentication_failed"), (403, "authentication_failed"),
    (301, "redirect_rejected"), (307, "redirect_rejected"),
    (400, "unexpected_http_status"), (404, "unexpected_http_status"),
])
def test_non_retryable_http_errors_are_safe(status, code, caplog, capsys):
    session = Session(Response(status, {"error": TEST_KEY}))
    delays = []
    with caplog.at_level(logging.DEBUG), pytest.raises(SourceError) as caught:
        fetch_models(TEST_KEY, session=session, sleep=delays.append)
    error = caught.value
    assert (error.code, error.http_status, error.attempts) == (code, status, 1)
    assert len(session.calls) == 1
    assert delays == []
    assert TEST_KEY not in str(error)
    assert TEST_KEY not in repr(error)
    assert TEST_KEY not in caplog.text
    captured = capsys.readouterr()
    assert TEST_KEY not in captured.out + captured.err


@pytest.mark.parametrize("failure,code,status", [
    (Response(429), "rate_limited", 429),
    (Response(500), "upstream_unavailable", 500),
    (Response(503), "upstream_unavailable", 503),
    (requests.Timeout(TEST_KEY), "timeout", None),
    (requests.ConnectionError(TEST_KEY), "network_error", None),
])
def test_retry_exhaustion_has_bounded_backoff_and_safe_error(failure, code, status):
    session = Session(failure, failure, failure)
    delays = []
    with pytest.raises(SourceError) as caught:
        fetch_models(TEST_KEY, session=session, sleep=delays.append)
    error = caught.value
    assert (error.code, error.http_status, error.attempts) == (code, status, 3)
    assert len(session.calls) == 3
    assert delays == [1, 2]
    assert TEST_KEY not in str(error)
    assert error.__suppress_context__


def test_retries_can_recover_after_different_failures():
    session = Session(requests.Timeout(TEST_KEY), Response(429), Response())
    delays = []
    payload, status, attempts = fetch_models(TEST_KEY, session=session, sleep=delays.append)
    assert payload["data"]
    assert (status, attempts) == (200, 3)
    assert delays == [1, 2]


@pytest.mark.parametrize("response,code", [
    (Response(error=ValueError(TEST_KEY)), "invalid_json"),
    (Response(payload={"unexpected": TEST_KEY}), "secret_in_response"),
    (Response(payload={TEST_KEY: "echoed as object key"}), "secret_in_response"),
    (Response(payload={"value": float("nan")}), "invalid_json"),
])
def test_invalid_or_secret_bearing_json_is_rejected(response, code):
    session = Session(response)
    with pytest.raises(SourceError) as caught:
        fetch_models(TEST_KEY, session=session)
    assert caught.value.code == code
    assert caught.value.http_status == 200
    assert caught.value.attempts == 1
    assert len(session.calls) == 1
    assert TEST_KEY not in str(caught.value)


def test_json_escaped_credential_echo_is_also_rejected():
    key = 'test-credential-"-with-\\-escape'
    session = Session(Response(payload={"message": "prefix " + key + " suffix"}))
    with pytest.raises(SourceError) as caught:
        fetch_models(key, session=session)
    assert caught.value.code == "secret_in_response"
    assert key not in str(caught.value)


@pytest.mark.parametrize("limit", [0, -1, 11, 1.5, True])
def test_invalid_attempt_limit_never_makes_a_request(limit):
    session = Session()
    with pytest.raises(ValueError):
        fetch_models(TEST_KEY, session=session, max_attempts=limit)
    assert session.calls == []


def test_single_attempt_limit_does_not_sleep():
    session = Session(Response(503))
    delays = []
    with pytest.raises(SourceError) as caught:
        fetch_models(TEST_KEY, session=session, sleep=delays.append, max_attempts=1)
    assert caught.value.attempts == 1
    assert delays == []


def test_config_reads_only_explicit_root_without_interpolation(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / ".env").write_text("ARTIFICIAL_ANALYSIS_API_KEY=parent-value\n", encoding="utf-8")
    (root / ".env").write_text("ARTIFICIAL_ANALYSIS_API_KEY=${INJECTED_KEY}\n", encoding="utf-8")
    monkeypatch.setenv("ARTIFICIAL_ANALYSIS_API_KEY", "environment-value")
    monkeypatch.setenv("INJECTED_KEY", "interpolated-value")
    assert load_api_key(root) == "${INJECTED_KEY}"


def test_missing_local_config_does_not_use_parent_or_environment(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / ".env").write_text(f"ARTIFICIAL_ANALYSIS_API_KEY={TEST_KEY}\n", encoding="utf-8")
    monkeypatch.setenv("ARTIFICIAL_ANALYSIS_API_KEY", TEST_KEY)
    with pytest.raises(SafeError) as caught:
        load_api_key(root)
    assert caught.value.code == "missing_config"
    assert TEST_KEY not in str(caught.value)


@pytest.mark.parametrize("content,code", [
    ("", "missing_api_key"),
    ("ARTIFICIAL_ANALYSIS_API_KEY=", "missing_api_key"),
    ("ARTIFICIAL_ANALYSIS_API_KEY", "missing_api_key"),
    ('ARTIFICIAL_ANALYSIS_API_KEY="bad key"', "invalid_api_key"),
    ('ARTIFICIAL_ANALYSIS_API_KEY="bad\\nkey"', "invalid_api_key"),
    ("ARTIFICIAL_ANALYSIS_API_KEY=含密钥", "invalid_api_key"),
])
def test_missing_and_invalid_keys_have_fixed_errors(tmp_path, content, code, capsys):
    (tmp_path / ".env").write_text(content, encoding="utf-8")
    with pytest.raises(SafeError) as caught:
        load_api_key(tmp_path)
    assert caught.value.code == code
    assert not capsys.readouterr().out


def test_unreadable_config_does_not_expose_parser_details(tmp_path):
    (tmp_path / ".env").write_bytes(b"ARTIFICIAL_ANALYSIS_API_KEY=\xff")
    with pytest.raises(SafeError) as caught:
        load_api_key(tmp_path)
    assert caught.value.code == "unreadable_config"
    assert caught.value.__suppress_context__


def test_config_accepts_utf8_bom(tmp_path):
    (tmp_path / ".env").write_text(
        f"ARTIFICIAL_ANALYSIS_API_KEY={TEST_KEY}\n", encoding="utf-8-sig"
    )
    assert load_api_key(tmp_path) == TEST_KEY


def test_dotenv_parse_warning_never_contains_configuration_text(tmp_path, caplog, capsys):
    # This malformed, synthetic credential produces a parser warning. Verify
    # the dependency reports a line number rather than the original statement.
    (tmp_path / ".env").write_text(
        f'ARTIFICIAL_ANALYSIS_API_KEY="{TEST_KEY}\n', encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING), pytest.raises(SafeError) as caught:
        load_api_key(tmp_path)
    assert caught.value.code == "missing_api_key"
    assert "line 1" in caplog.text
    assert TEST_KEY not in caplog.text
    captured = capsys.readouterr()
    assert TEST_KEY not in captured.out + captured.err + str(caught.value)


@pytest.mark.parametrize("status,label", [(401, "401"), (403, "403"), (429, "429"), (503, "5xx")])
def test_http_errors_have_fixed_chinese_explanation_and_status(status, label):
    session = Session(Response(status))
    with pytest.raises(SourceError) as caught:
        fetch_models(TEST_KEY, session=session, max_attempts=1)
    assert label in caught.value.message
    assert caught.value.http_status == status
    assert any("\u4e00" <= char <= "\u9fff" for char in caught.value.message)
