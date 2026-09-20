"""One-shot Modex transport. No config loading, database access or auto retry.

Only the explicitly selected endpoint/model is supported. Response bytes and
JSON are bounded before the existing briefing validator checks cited facts.
"""
import json
import math
import re
from threading import Lock
from time import perf_counter

import requests
from requests.adapters import HTTPAdapter
from requests.auth import AuthBase
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

from .briefing import (AnalysisClientError, AnalysisResponse, AnalysisSettings,
                       MAX_RESPONSE_BYTES, check_settings, build_prompt, _load_json, _validate_pack)


MODEX_BASE_URL = 'https://hk.modex-ai.cloud/v1'
MODEX_ENDPOINT = MODEX_BASE_URL + '/chat/completions'
MODEX_MODEL = 'gpt-5.6-sol'
MAX_HTTP_RESPONSE_BYTES = 128 * 1024
CONNECT_TIMEOUT_SECONDS = 10
MAX_PROMPT_BYTES = 16 * 1024
USAGE_FIELDS = ('prompt_tokens', 'completion_tokens', 'total_tokens')


def normalize_modex_base_url(value: str) -> str:
    """Reject roots, full endpoints and all unselected origins or URL variants."""
    if value not in (MODEX_BASE_URL, MODEX_BASE_URL + '/'):
        raise AnalysisClientError('config_error') from None
    return MODEX_BASE_URL


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def build_request_body(pack: dict, prompt: str) -> dict:
    # V2 already includes the exact one-claim instruction. Keep one canonical
    # pack and the existing transport contract; no second wire fact schema.
    system_prompt = prompt if pack.get('scope', {}).get('pack_version') in ('m2c-selected-v2', 'm3-daily-v1') else (
        prompt + '\n请简短输出，每个部分一条中文结论。')
    return {'model': MODEX_MODEL, 'messages': [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': _canonical(pack)},
    ], 'stream': False}


def request_body_size(pack: dict, settings: AnalysisSettings) -> int:
    """Exact Requests JSON encoding size, without sending or touching settings files."""
    body = build_request_body(pack, build_prompt(pack, settings))
    return len(requests.models.complexjson.dumps(body, allow_nan=False).encode('utf-8'))


def _response_model(value, api_key: str) -> str | None:
    # Unknown upstream prose must never become a diagnostic or a log entry.
    if (not isinstance(value, str) or api_key in value
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', value) is None):
        return None
    return value


def _usage(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('invalid usage')
    selected = {}
    for field in USAGE_FIELDS:
        if field in value:
            number = value[field]
            if type(number) is not int or number < 0:
                raise ValueError('invalid usage')
            selected[field] = number
    return selected or None


class _BearerAuth(AuthBase):
    """Explicit auth prevents Requests from consulting ~/.netrc credentials."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def __call__(self, request):
        request.headers['Authorization'] = 'Bearer ' + self._api_key
        return request


def _wrapped_timeout(error: Exception) -> bool:
    # iter_content may wrap urllib3 ReadTimeoutError in ConnectionError. Inspect
    # exception types only, never parse or expose provider exception messages.
    return any(isinstance(value, (requests.Timeout, Urllib3TimeoutError))
               for value in (error.__cause__, *error.args))


class ModexClient:
    """Lazy, single-POST client; injected sessions are owned by their caller."""

    def __init__(self, settings: AnalysisSettings, *, session=None):
        self._settings = settings
        self._session = session
        self._owned_session = None
        self._used = False
        self._closed = False
        self._lock = Lock()
        self.last_request_body_bytes = None

    def close(self) -> None:
        self._closed = True
        owned, self._owned_session = self._owned_session, None
        if owned is not None:
            try:
                owned.close()
            except Exception:
                pass

    def _preflight(self, pack: dict, prompt: str, timeout_seconds: float) -> dict:
        try:
            settings = self._settings
            if not isinstance(settings, AnalysisSettings) or check_settings(settings):
                raise ValueError('blocked')
            normalize_modex_base_url(settings.base_url)
            if settings.model != MODEX_MODEL:
                raise ValueError('unsupported model')
            key = settings.api_key
            if (not isinstance(key, str) or not key or len(key) > 2048
                    or any(ord(character) < 33 or ord(character) > 126 for character in key)):
                raise ValueError('invalid credential')
            if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                    or not 0 < timeout_seconds <= 120 or timeout_seconds != settings.timeout_seconds):
                raise ValueError('invalid timeout')
            if (not isinstance(prompt, str) or not prompt.strip()
                    or len(prompt.encode('utf-8')) > MAX_PROMPT_BYTES or key in prompt):
                raise ValueError('invalid prompt')
            checked = _validate_pack(pack, settings)
            if settings.input_mode == 'synthetic':
                from .synthetic_briefing import build_synthetic_pack
                if _canonical(checked) != _canonical(build_synthetic_pack()):
                    raise ValueError('not fixed synthetic input')
            elif settings.input_mode != 'real' or checked['source'] != 'artificial_analysis':
                raise ValueError('invalid real input')
            return checked
        except Exception:
            raise AnalysisClientError('config_error') from None

    def generate(self, pack: dict, *, prompt: str, timeout_seconds: float) -> AnalysisResponse:
        checked = self._preflight(pack, prompt, timeout_seconds)
        with self._lock:
            if self._used or self._closed:
                raise AnalysisClientError('config_error') from None
            self._used = True
        response = None
        started = perf_counter()
        http_status = None
        response_model = None

        def error(code: str):
            elapsed = max(0.0, round(perf_counter() - started, 3))
            return AnalysisClientError(code, http_status=http_status,
                                       elapsed_seconds=elapsed, response_model=response_model)

        try:
            session = self._session
            if session is None:
                session = requests.Session()
                self._owned_session = session
                session.mount('https://', HTTPAdapter(max_retries=0))
                session.mount('http://', HTTPAdapter(max_retries=0))
            elif isinstance(session, requests.sessions.Session):
                # A deliberately injected real Session also cannot retain an
                # adapter retry policy for this one-shot destination.
                session.mount(MODEX_BASE_URL + '/', HTTPAdapter(max_retries=0))
            body = build_request_body(checked, prompt)
            self.last_request_body_bytes = len(requests.models.complexjson.dumps(body, allow_nan=False).encode('utf-8'))
            response = session.post(
                MODEX_ENDPOINT, json=body,
                headers={'Authorization': 'Bearer ' + self._settings.api_key,
                         'Content-Type': 'application/json', 'Accept': 'application/json'},
                auth=_BearerAuth(self._settings.api_key),
                timeout=(CONNECT_TIMEOUT_SECONDS, timeout_seconds),
                allow_redirects=False, verify=True, stream=True,
            )
            # transport stream=True bounds downloaded bytes; protocol stream is
            # explicitly false and SSE/streamed model output is never accepted.
            code = response.status_code
            if type(code) is not int or not 100 <= code <= 599:
                raise error('invalid_response')
            http_status = code
            if 300 <= code < 400:
                raise error('redirect')
            if code in (401, 403):
                raise error('auth_error')
            if code == 404:
                raise error('model_unavailable')
            if code == 429:
                raise error('rate_limited')
            if 500 <= code <= 599:
                raise error('server_error')
            if code != 200:
                raise error('invalid_response')
            content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            if content_type and content_type != 'application/json':
                raise error('invalid_response')
            declared_length = response.headers.get('Content-Length')
            if declared_length is not None:
                try:
                    length = int(declared_length)
                except (TypeError, ValueError, OverflowError):
                    raise error('invalid_response') from None
                if length < 0:
                    raise error('invalid_response')
                if length > MAX_HTTP_RESPONSE_BYTES:
                    raise error('response_too_large')
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not isinstance(chunk, bytes):
                    raise error('invalid_response')
                size += len(chunk)
                if size > MAX_HTTP_RESPONSE_BYTES:
                    raise error('response_too_large')
                chunks.append(chunk)
            try:
                data = _load_json(b''.join(chunks).decode('utf-8'), MAX_HTTP_RESPONSE_BYTES)
                if not isinstance(data, dict):
                    raise ValueError('not object')
                response_model = _response_model(data.get('model'), self._settings.api_key)
                if response_model is None:
                    raise ValueError('invalid model')
                if response_model != MODEX_MODEL:
                    raise error('model_mismatch')
                choices = data.get('choices')
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError('invalid choices')
                choice = choices[0]
                if (not isinstance(choice, dict) or choice.get('finish_reason') != 'stop'
                        or ('index' in choice and (type(choice['index']) is not int or choice['index'] != 0))):
                    raise ValueError('incomplete result')
                message = choice.get('message')
                if (not isinstance(message, dict) or message.get('role') != 'assistant'
                        or message.get('tool_calls') is not None or message.get('function_call') is not None
                        or message.get('refusal') not in (None, '')):
                    raise ValueError('unsupported response')
                content = message.get('content')
                if not isinstance(content, str) or not content.strip() or self._settings.api_key in content:
                    raise ValueError('missing or unsafe content')
                if not isinstance(_load_json(content, MAX_RESPONSE_BYTES), dict):
                    raise ValueError('not JSON object')
                usage = _usage(data.get('usage'))
            except AnalysisClientError:
                raise
            except Exception:
                raise error('invalid_response') from None
            return AnalysisResponse(content=content, request_model=MODEX_MODEL, response_model=response_model,
                                    usage=usage, http_status=http_status,
                                    elapsed_seconds=max(0.0, round(perf_counter() - started, 3)))
        except AnalysisClientError:
            raise
        except (requests.Timeout, TimeoutError):
            raise error('timeout') from None
        except requests.RequestException as exc:
            raise error('timeout' if _wrapped_timeout(exc) else 'network_error') from None
        except Exception:
            raise error('client_error') from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            self.close()
