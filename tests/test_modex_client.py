"""Modex adapter exercised exclusively through fake sessions and fixed facts."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest
import requests

from benchmark_dashboard import modex_client
from benchmark_dashboard.briefing import (AnalysisClientError, AnalysisSettings, PROMPT,
                                         generate_briefing)
from benchmark_dashboard.fact_pack import compute_fact_hash
from benchmark_dashboard.modex_client import (MODEX_BASE_URL, MODEX_ENDPOINT, MODEX_MODEL,
                                              ModexClient, normalize_modex_base_url)
from benchmark_dashboard.synthetic_briefing import build_synthetic_pack


FAKE_SECRET = 'synthetic-modex-credential-not-real'


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('A Modex unit test attempted a real HTTP request')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)


@pytest.fixture
def settings():
    return AnalysisSettings(enabled=True, base_url=MODEX_BASE_URL, model=MODEX_MODEL,
                            api_key=FAKE_SECRET, real_integration_authorized=True,
                            protocol_verified=True, input_mode='synthetic', timeout_seconds=120)


def result_content(pack=None):
    pack = pack or build_synthetic_pack()
    return json.dumps({
        'source': pack['source'], 'snapshot_hash': pack['snapshot']['content_hash'],
        'fact_hash': pack['fact_hash'], 'model': MODEX_MODEL,
        'sections': [
            {'key': 'current', 'claims': [{'text': '合成数据覆盖 1/2，仅用于接口联调。',
                                         'fact_ids': ['synthetic-coverage']}]},
            {'key': 'changes', 'claims': [{'text': '当前是初始基线，暂无历史可比较。',
                                         'fact_ids': ['synthetic-changes']}]},
            {'key': 'limitations', 'claims': [{'text': '合成记录的单位、版本和配置未知，非公司实测。',
                                             'fact_ids': ['synthetic-limitations']}]},
        ],
    }, ensure_ascii=False)


def response_body():
    return {'model': MODEX_MODEL, 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': result_content()}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}


class FakeResponse:
    def __init__(self, *, body=None, status=200, raw=None, headers=None, chunks=None, error=None):
        self.status_code = status
        self.headers = headers if headers is not None else {'Content-Type': 'application/json'}
        self.raw = raw if raw is not None else json.dumps(
            body if body is not None else response_body(), ensure_ascii=False).encode('utf-8')
        self.chunks = chunks
        self.error = error
        self.closed = False
        self.reads = 0

    def iter_content(self, chunk_size):
        self.reads += 1
        assert chunk_size == 8192
        if self.error:
            raise self.error
        yield from self.chunks if self.chunks is not None else [self.raw]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response=None, *, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []
        self.adapters = {}
        self.closed = False

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


def call(client, pack=None):
    return client.generate(pack or build_synthetic_pack(), prompt=PROMPT, timeout_seconds=120)


def test_one_exact_post_with_isolated_key_minimal_protocol_and_timeouts(settings, monkeypatch):
    monkeypatch.setenv('ARTIFICIAL_ANALYSIS_API_KEY', 'synthetic-source-key-never-use')
    session = FakeSession()
    client = ModexClient(settings, session=session)
    result = call(client)
    assert result.request_model == result.response_model == MODEX_MODEL
    assert result.http_status == 200 and result.elapsed_seconds >= 0
    assert result.usage == {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}
    assert len(session.calls) == 1
    url, request = session.calls[0]
    assert url == MODEX_ENDPOINT
    assert request['headers']['Authorization'] == 'Bearer ' + FAKE_SECRET
    assert request['timeout'] == (10, 120)
    assert request['allow_redirects'] is False and request['verify'] is True
    assert request['stream'] is True  # bounded HTTP reading, not model streaming
    body = request['json']
    assert set(body) == {'model', 'messages', 'stream'}
    assert body['model'] == MODEX_MODEL and body['stream'] is False
    assert [item['role'] for item in body['messages']] == ['system', 'user']
    assert json.loads(body['messages'][1]['content']) == build_synthetic_pack()
    assert FAKE_SECRET not in json.dumps(body)
    assert 'synthetic-source-key-never-use' not in str(request)
    assert session.response.closed and not session.closed
    with pytest.raises(AnalysisClientError):
        call(client)
    assert len(session.calls) == 1
    client.close()
    assert not session.closed


def test_owned_session_has_zero_transport_retry_and_is_closed(settings, monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(modex_client.requests, 'Session', lambda: session)
    client = ModexClient(settings)
    assert not session.adapters and not session.calls
    call(client)
    assert all(adapter.max_retries.total == 0 for adapter in session.adapters.values())
    assert set(session.adapters) == {'https://', 'http://'}
    assert session.closed and session.response.closed
    client.close()


def test_explicit_bearer_auth_prevents_netrc_fallback_without_disabling_proxy_environment(settings, monkeypatch):
    fake = FakeSession()
    call(ModexClient(settings, session=fake))
    _, request = fake.calls[0]
    def forbidden(*args, **kwargs):
        pytest.fail('Modex auth must not fall back to global netrc credentials')
    monkeypatch.setattr(requests.sessions, 'get_netrc_auth', forbidden)
    with requests.Session() as preparation_only:
        assert preparation_only.trust_env is True
        prepared = preparation_only.prepare_request(requests.Request(
            'POST', MODEX_ENDPOINT, headers=request['headers'], json=request['json'], auth=request['auth']))
    assert prepared.headers['Authorization'] == 'Bearer ' + FAKE_SECRET
    assert FAKE_SECRET not in repr(request['auth'])


def test_injected_real_session_cannot_retain_a_retrying_adapter(settings, monkeypatch):
    from requests.adapters import HTTPAdapter
    response = FakeResponse()
    calls = []
    with requests.Session() as session:
        session.mount('https://', HTTPAdapter(max_retries=5))
        def fake_post(url, **kwargs):
            calls.append(url)
            assert session.get_adapter(url).max_retries.total == 0
            return response
        monkeypatch.setattr(session, 'post', fake_post)
        call(ModexClient(settings, session=session))
    assert calls == [MODEX_ENDPOINT]


@pytest.mark.parametrize('base', [
    'https://hk.modex-ai.cloud', 'https://hk.modex-ai.cloud/',
    MODEX_ENDPOINT, MODEX_BASE_URL + '/v1', MODEX_BASE_URL + '//',
    'https://hk.modex-ai.cloud//v1', 'https://hk.modex-ai.cloud:443/v1',
    'https://user:password@hk.modex-ai.cloud/v1', MODEX_BASE_URL + '?key=fake',
    MODEX_BASE_URL + '#fragment', 'http://hk.modex-ai.cloud/v1',
    'https://another.invalid/v1', 'https://hk.modex-ai.cloud.evil.invalid/v1',
    'https://hk.modex-ai.cloud/v%31', ' ' + MODEX_BASE_URL, None,
])
def test_wrong_url_never_calls(settings, base):
    session = FakeSession()
    client = ModexClient(replace(settings, base_url=base), session=session)
    with pytest.raises(AnalysisClientError):
        call(client)
    assert not session.calls


def test_only_one_trailing_slash_is_normalized(settings):
    assert normalize_modex_base_url(MODEX_BASE_URL + '/') == MODEX_BASE_URL
    session = FakeSession()
    call(ModexClient(replace(settings, base_url=MODEX_BASE_URL + '/'), session=session))
    assert session.calls[0][0] == MODEX_ENDPOINT


@pytest.mark.parametrize('mutation', [
    {'enabled': False}, {'enabled': 'true'}, {'api_key': ''},
    {'api_key': 'invalid\r\nheader'}, {'api_key': ' leading-space'},
    {'real_integration_authorized': False}, {'protocol_verified': False},
    {'model': 'gpt-5.6'}, {'model': ''}, {'input_mode': 'other'},
    {'input_mode': 'real', 'data_use_confirmed': False},
    {'input_mode': 'real', 'data_use_confirmed': True, 'purpose_authorized': False},
    {'input_mode': 'real', 'data_use_confirmed': True, 'purpose_authorized': True,
     'transmission_authorized': False},
])
def test_missing_settings_authorization_and_key_prevent_all_calls(settings, mutation):
    session = FakeSession()
    with pytest.raises(AnalysisClientError):
        call(ModexClient(replace(settings, **mutation), session=session))
    assert not session.calls


@pytest.mark.parametrize('timeout', [0, 121, float('inf'), True, 20, '120'])
def test_direct_call_timeout_cannot_override_validated_settings(settings, timeout):
    session = FakeSession()
    with pytest.raises(AnalysisClientError):
        ModexClient(settings, session=session).generate(
            build_synthetic_pack(), prompt=PROMPT, timeout_seconds=timeout)
    assert not session.calls


def test_synthetic_source_cannot_hide_real_or_modified_input(settings):
    pack = build_synthetic_pack()
    pack['facts'][0]['text'] = '伪装为合成的任意数据。'
    pack['fact_hash'] = compute_fact_hash(pack)
    session = FakeSession()
    with pytest.raises(AnalysisClientError):
        call(ModexClient(settings, session=session), pack)
    real_settings = replace(settings, input_mode='real', data_use_confirmed=True,
                            purpose_authorized=True, transmission_authorized=True)
    with pytest.raises(AnalysisClientError):
        call(ModexClient(real_settings, session=session), build_synthetic_pack())
    assert not session.calls


@pytest.mark.parametrize('status,code', [(301, 'redirect'), (302, 'redirect'), (307, 'redirect'),
    (401, 'auth_error'), (403, 'auth_error'), (404, 'model_unavailable'),
    (429, 'rate_limited'), (500, 'server_error'), (503, 'server_error'), (400, 'invalid_response')])
def test_http_failures_are_fixed_safe_and_never_retry(settings, status, code):
    response = FakeResponse(status=status, raw=FAKE_SECRET.encode())
    session = FakeSession(response)
    client = ModexClient(settings, session=session)
    with pytest.raises(AnalysisClientError) as caught:
        call(client)
    assert caught.value.code == code and caught.value.http_status == status
    assert FAKE_SECRET not in str(caught.value) + repr(caught.value)
    assert response.reads == 0 and response.closed and len(session.calls) == 1
    with pytest.raises(AnalysisClientError):
        call(client)
    assert len(session.calls) == 1


@pytest.mark.parametrize('exception,code', [
    (requests.Timeout(FAKE_SECRET), 'timeout'), (TimeoutError(FAKE_SECRET), 'timeout'),
    (requests.ConnectionError(FAKE_SECRET), 'network_error'),
    (requests.exceptions.SSLError(FAKE_SECRET), 'network_error'),
    (RuntimeError(FAKE_SECRET), 'client_error'),
])
def test_transport_errors_are_sanitized_no_retry_and_owned_session_closed(settings, monkeypatch, exception, code):
    session = FakeSession(error=exception)
    monkeypatch.setattr(modex_client.requests, 'Session', lambda: session)
    with pytest.raises(AnalysisClientError) as caught:
        call(ModexClient(settings))
    assert caught.value.code == code and caught.value.http_status is None
    assert FAKE_SECRET not in str(caught.value) + repr(caught.value)
    assert len(session.calls) == 1 and session.closed


def test_wrapped_read_timeout_is_classified_without_parsing_secret_message(settings):
    from urllib3.exceptions import ReadTimeoutError
    response = FakeResponse(error=requests.ConnectionError(ReadTimeoutError(None, None, FAKE_SECRET)))
    session = FakeSession(response)
    with pytest.raises(AnalysisClientError) as caught:
        call(ModexClient(settings, session=session))
    assert caught.value.code == 'timeout' and caught.value.http_status == 200
    assert FAKE_SECRET not in str(caught.value)
    assert response.closed and len(session.calls) == 1


@pytest.mark.parametrize('model,safe_model,code', [(MODEX_MODEL + '-other', MODEX_MODEL + '-other', 'model_mismatch'),
    (FAKE_SECRET, None, 'invalid_response'), ('<html>unsafe</html>', None, 'invalid_response'),
    (None, None, 'invalid_response'), (True, None, 'invalid_response')])
def test_upstream_model_is_distinct_and_untrusted_text_is_removed(settings, model, safe_model, code):
    body = response_body()
    body['model'] = model
    session = FakeSession(FakeResponse(body=body))
    with pytest.raises(AnalysisClientError) as caught:
        call(ModexClient(settings, session=session))
    assert caught.value.code == code and caught.value.response_model == safe_model
    assert FAKE_SECRET not in str(caught.value) + repr(caught.value) + str(vars(caught.value))
    assert len(session.calls) == 1


@pytest.mark.parametrize('mutation', ['no_choices', 'empty_choices', 'many_choices', 'wrong_choice',
    'wrong_index', 'truncated', 'tool_finish', 'no_message', 'wrong_role', 'tool_call', 'function_call',
    'refusal', 'no_content', 'empty_content', 'list_content', 'html_content', 'malformed_json',
    'duplicate_json', 'nonfinite_json', 'array_json', 'secret_content'])
def test_unusable_completion_is_rejected(settings, mutation):
    body = response_body()
    choice = body['choices'][0]
    message = choice['message']
    if mutation == 'no_choices': del body['choices']
    elif mutation == 'empty_choices': body['choices'] = []
    elif mutation == 'many_choices': body['choices'] *= 2
    elif mutation == 'wrong_choice': body['choices'] = [None]
    elif mutation == 'wrong_index': choice['index'] = True
    elif mutation == 'truncated': choice['finish_reason'] = 'length'
    elif mutation == 'tool_finish': choice['finish_reason'] = 'tool_calls'
    elif mutation == 'no_message': del choice['message']
    elif mutation == 'wrong_role': message['role'] = 'tool'
    elif mutation == 'tool_call': message['tool_calls'] = [{'id': 'test'}]
    elif mutation == 'function_call': message['function_call'] = {'name': 'test'}
    elif mutation == 'refusal': message['refusal'] = 'denied'
    elif mutation == 'no_content': del message['content']
    elif mutation == 'empty_content': message['content'] = ' '
    elif mutation == 'list_content': message['content'] = []
    elif mutation == 'html_content': message['content'] = '<html>not JSON</html>'
    elif mutation == 'malformed_json': message['content'] = '{'
    elif mutation == 'duplicate_json': message['content'] = '{"x":1,"x":2}'
    elif mutation == 'nonfinite_json': message['content'] = '{"x":NaN}'
    elif mutation == 'array_json': message['content'] = '[]'
    elif mutation == 'secret_content': message['content'] = json.dumps({'text': FAKE_SECRET})
    session = FakeSession(FakeResponse(body=body))
    with pytest.raises(AnalysisClientError) as caught:
        call(ModexClient(settings, session=session))
    assert caught.value.code == 'invalid_response'
    assert session.response.closed and len(session.calls) == 1


@pytest.mark.parametrize('raw', [b'{', b'{"model":"x","model":"y"}', b'{"x":NaN}',
                                 b'{"x":Infinity}', b'[]', b'\xff'])
def test_outer_json_is_strict(settings, raw):
    with pytest.raises(AnalysisClientError) as caught:
        call(ModexClient(settings, session=FakeSession(FakeResponse(raw=raw))))
    assert caught.value.code == 'invalid_response'


def test_response_is_bounded_by_headers_and_streamed_bytes(settings):
    limit = modex_client.MAX_HTTP_RESPONSE_BYTES
    for response in [FakeResponse(headers={'Content-Length': str(limit + 1)}),
                     FakeResponse(chunks=[b'x' * limit, b'x'])]:
        session = FakeSession(response)
        with pytest.raises(AnalysisClientError) as caught:
            call(ModexClient(settings, session=session))
        assert caught.value.code == 'response_too_large'
        assert response.closed and len(session.calls) == 1


@pytest.mark.parametrize('headers', [{'Content-Type': 'text/html'}, {'Content-Type': 'text/event-stream'},
    {'Content-Length': '-1'}, {'Content-Length': 'not-an-integer'}])
def test_bad_http_metadata_is_rejected_before_read(settings, headers):
    response = FakeResponse(headers=headers)
    with pytest.raises(AnalysisClientError) as caught:
        call(ModexClient(settings, session=FakeSession(response)))
    assert caught.value.code == 'invalid_response' and response.reads == 0


@pytest.mark.parametrize('usage', [None, {}, {'unrecognized': FAKE_SECRET}, {'prompt_tokens': 0}])
def test_usage_missing_stays_unknown_and_unknown_fields_not_copied(settings, usage):
    body = response_body()
    body['usage'] = usage
    result = call(ModexClient(settings, session=FakeSession(FakeResponse(body=body))))
    assert result.usage == ({'prompt_tokens': 0} if usage == {'prompt_tokens': 0} else None)


@pytest.mark.parametrize('usage', [True, '12', [], {'prompt_tokens': True}, {'total_tokens': -1},
                                   {'completion_tokens': '5'}, {'completion_tokens': 1.5}])
def test_known_usage_fields_must_be_nonnegative_integers(settings, usage):
    body = response_body()
    body['usage'] = usage
    with pytest.raises(AnalysisClientError) as caught:
        call(ModexClient(settings, session=FakeSession(FakeResponse(body=body))))
    assert caught.value.code == 'invalid_response'


def test_core_rejects_forged_reference_without_publishing_or_retry(settings, tmp_path):
    body = response_body()
    content = json.loads(body['choices'][0]['message']['content'])
    content['sections'][0]['claims'][0]['fact_ids'] = ['forged-id']
    body['choices'][0]['message']['content'] = json.dumps(content, ensure_ascii=False)
    session = FakeSession(FakeResponse(body=body))
    result = generate_briefing(build_synthetic_pack(), settings, ModexClient(settings, session=session),
                               cache_dir=tmp_path / 'isolated-synthetic-results')
    assert result['status'] == 'failed' and result['result'] is None
    assert len(session.calls) == 1


def test_direct_client_does_not_read_configuration_or_database(settings, monkeypatch):
    from pathlib import Path
    import sqlite3

    def forbidden(*args, **kwargs):
        pytest.fail('transport must not load local configuration or business data')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    result = call(ModexClient(settings, session=FakeSession()))
    assert result.http_status == 200
