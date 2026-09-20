"""Artificial Analysis HTTP client with bounded retries and safe errors."""

import json
import time
from collections.abc import Callable
from typing import Any

import requests


API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
SOURCE = "artificial_analysis"


class SourceError(Exception):
    """Only fixed public messages belong in this exception."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int | None = None,
        attempts: int = 0,
    ):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.attempts = attempts
        super().__init__(message)


def fetch_models(
    api_key: str,
    *,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
) -> tuple[Any, int, int]:
    """Fetch models, returning ``(payload, HTTP status, attempt count)``.

    The response body, headers, URLs, credentials, and requests exception
    details must never be embedded in errors. Redirects are disabled so the
    credential cannot be forwarded to an unexpected destination.
    """
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts 必须是 1 至 10 之间的整数。")
    if not isinstance(api_key, str) or not api_key or any(not 33 <= ord(char) <= 126 for char in api_key):
        raise SourceError("invalid_api_key", "API Key 格式无效，请在本机检查 .env 配置。")

    client = session if session is not None else requests.Session()
    try:
        for attempt in range(1, max_attempts + 1):
            error: SourceError
            try:
                response = client.get(
                    API_URL,
                    headers={"x-api-key": api_key, "Accept": "application/json"},
                    timeout=(10, 45),
                    allow_redirects=False,
                )
            except requests.Timeout:
                error = SourceError("timeout", "API 请求超时，已达到重试次数上限。", attempts=attempt)
            except requests.RequestException:
                error = SourceError("network_error", "API 网络连接失败，已达到重试次数上限。", attempts=attempt)
            else:
                status = response.status_code
                if 200 <= status < 300:
                    try:
                        payload = response.json()
                        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                    except (ValueError, TypeError, OverflowError):
                        raise SourceError("invalid_json", "API 返回的 JSON 无效，本次响应未保存。", status, attempt) from None
                    # Check both plain and JSON-escaped representations so a
                    # credential containing quotes/backslashes cannot evade
                    # the safety check when echoed inside a JSON string.
                    escaped_key = json.dumps(api_key, ensure_ascii=False)[1:-1]
                    if api_key in serialized or escaped_key in serialized:
                        raise SourceError("secret_in_response", "API 响应未通过密钥安全检查，本次响应未保存。", status, attempt)
                    return payload, status, attempt
                if status == 401:
                    raise SourceError("authentication_failed", "API 认证失败（HTTP 401），请在本机检查 API Key。", status, attempt)
                if status == 403:
                    raise SourceError("authentication_failed", "API 访问被拒绝（HTTP 403），请检查账号或 API Key 权限。", status, attempt)
                if status == 429:
                    error = SourceError("rate_limited", "API 请求频率受限（HTTP 429），请稍后重试。", status, attempt)
                elif 500 <= status < 600:
                    error = SourceError("upstream_unavailable", "API 服务暂不可用（HTTP 5xx），请稍后重试。", status, attempt)
                elif 300 <= status < 400:
                    raise SourceError("redirect_rejected", "API 返回了重定向，已停止请求以保护密钥。", status, attempt)
                else:
                    raise SourceError("unexpected_http_status", "API 返回了非预期的 HTTP 状态，本次响应未保存。", status, attempt)

            if attempt == max_attempts:
                raise error from None
            sleep(2 ** (attempt - 1))
    finally:
        if session is None:
            client.close()

    raise AssertionError("Unreachable retry state.")
