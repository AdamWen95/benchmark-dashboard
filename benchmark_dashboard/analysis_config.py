"""Explicit Modex CLI configuration boundary; never imported as an auto-loader.

Only ``load_analysis_settings`` reads a file, and only its caller-selected root's
``.env``. UI rendering and default prechecks must use default AnalysisSettings
instead. No environment fallback, interpolation or Artificial Analysis key is
supported. Protocol selection below describes the local adapter contract, not
evidence that a live Modex request or its output has passed validation.
"""
from pathlib import Path
import re

from .briefing import AnalysisSettings


MODEX_BASE_URL = 'https://hk.modex-ai.cloud/v1'
MODEX_MODEL = 'gpt-5.6-sol'
ALLOWED_KEYS = frozenset({'ANALYSIS_ENABLED', 'ANALYSIS_DATA_USE_CONFIRMED',
                          'ANALYSIS_BASE_URL', 'ANALYSIS_MODEL', 'ANALYSIS_API_KEY'})
MAX_CONFIG_BYTES = 1024 * 1024
MAX_ANALYSIS_LINE = 8192
MESSAGES = {
    'missing_config': '本机分析配置文件尚不存在，请在本机配置后再执行显式生成。',
    'config_unreadable': '本机分析配置无法读取，请检查文件状态与编码。',
    'config_missing': '分析服务地址或模型尚未配置，请核对本机分析配置。',
    'invalid_config': '本机分析配置格式无效，请检查分析配置项、重复项和单行语法。',
    'service_mismatch': '分析服务地址与本次指定的 Modex Base URL 不一致，未发起请求。',
    'model_mismatch': '分析模型与本次指定的模型 ID 不一致，未发起请求。',
    'invalid_api_key': '独立分析密钥格式无效，请在本机检查；未发起请求。',
    'invalid_arguments': '显式分析操作参数无效，未读取配置或发起请求。',
}


class AnalysisConfigError(ValueError):
    """A fixed public code/message; arbitrary arguments can never be echoed."""

    def __init__(self, code: str = 'invalid_config'):
        self.code = code if isinstance(code, str) and code in MESSAGES else 'invalid_config'
        super().__init__(MESSAGES[self.code])


def _parse_value(value: str) -> str:
    value = value.strip(' \t')
    if not value:
        return ''
    if value[0] in ('"', "'"):
        quote = value[0]
        end = value.find(quote, 1)
        if end < 0:
            raise AnalysisConfigError()
        tail = value[end + 1:].strip(' \t')
        if tail and not tail.startswith('#'):
            raise AnalysisConfigError()
        # Quoted text stays literal. No escape decoding or variable expansion.
        return value[1:end]
    if '"' in value or "'" in value:
        raise AnalysisConfigError()
    return re.split(r'[ \t]+#', value, maxsplit=1)[0].strip(' \t')


def _read_values(path: Path) -> dict[str, str]:
    values = {}
    failure_code = None
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise AnalysisConfigError()
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            for raw_line in handle:
                # Ignore all non-analysis lines before parsing or retaining any
                # value. In particular the source-data API key never enters the
                # returned mapping, settings or any error/log.
                line = raw_line.lstrip(' \t')
                if line.startswith('export '):
                    if line[len('export '):].lstrip(' \t').startswith('ANALYSIS_'):
                        raise AnalysisConfigError()
                    continue
                if not line.startswith('ANALYSIS_'):
                    continue
                if len(line) > MAX_ANALYSIS_LINE:
                    raise AnalysisConfigError()
                key, separator, value = line.rstrip('\r\n').partition('=')
                key = key.strip(' \t')
                if not separator or key not in ALLOWED_KEYS or key in values:
                    raise AnalysisConfigError()
                values[key] = _parse_value(value)
    except FileNotFoundError:
        failure_code = 'missing_config'
    except (OSError, UnicodeError):
        failure_code = 'config_unreadable'
    # Raise outside the handler so the public error does not retain an original
    # I/O or decoder exception (which could contain source text) as __context__.
    if failure_code is not None:
        raise AnalysisConfigError(failure_code)
    return values


def _boolean(values: dict[str, str], key: str) -> bool:
    if key not in values:
        return False
    value = values[key].lower()
    if value not in ('true', 'false'):
        raise AnalysisConfigError()
    return value == 'true'


def load_analysis_settings(root: Path, *, input_mode='real', enable_once=False,
                           one_request_authorized=False, purpose_authorized=False,
                           transmission_authorized=False) -> AnalysisSettings:
    """Explicitly load only independent analysis settings from root/.env.

    ``enable_once`` enables this returned settings object only. The persistent
    data-use flag is never inferred from a key, input mode or request permission.
    The caller's one-request permission remains separate from purpose and
    transmission permission. Blank analysis keys stay blank for the core gate to
    reject; they never fall back to collection or process-global credentials.
    """
    if (not isinstance(root, Path) or input_mode not in ('synthetic', 'real')
            or any(type(value) is not bool for value in
                   (enable_once, one_request_authorized, purpose_authorized, transmission_authorized))):
        raise AnalysisConfigError('invalid_arguments')
    values = _read_values(root / '.env')
    base_url, model = values.get('ANALYSIS_BASE_URL', ''), values.get('ANALYSIS_MODEL', '')
    if not base_url or not model:
        raise AnalysisConfigError('config_missing')
    if base_url != MODEX_BASE_URL:
        raise AnalysisConfigError('service_mismatch')
    if model != MODEX_MODEL:
        raise AnalysisConfigError('model_mismatch')
    api_key = values.get('ANALYSIS_API_KEY', '')
    if len(api_key) > 2048 or any(not 33 <= ord(character) <= 126 for character in api_key):
        raise AnalysisConfigError('invalid_api_key')
    enabled = _boolean(values, 'ANALYSIS_ENABLED')
    data_use_confirmed = _boolean(values, 'ANALYSIS_DATA_USE_CONFIRMED')
    return AnalysisSettings(enabled=enabled or enable_once, data_use_confirmed=data_use_confirmed,
                            base_url=base_url, model=model, api_key=api_key,
                            purpose_authorized=purpose_authorized,
                            transmission_authorized=transmission_authorized,
                            real_integration_authorized=one_request_authorized,
                            protocol_verified=True, timeout_seconds=120, input_mode=input_mode)
