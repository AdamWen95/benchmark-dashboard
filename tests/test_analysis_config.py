"""Only temporary synthetic .env files are used; no project secrets or network."""
from pathlib import Path

import pytest

from benchmark_dashboard.analysis_config import AnalysisConfigError, load_analysis_settings


BASE_URL = 'https://hk.modex-ai.cloud/v1'
MODEL = 'gpt-5.6-sol'
KEY = 'SYNTHETIC-MODEX-KEY'


def write_config(tmp_path, *, base=BASE_URL, model=MODEL, key=KEY, extra=''):
    content = f'ANALYSIS_BASE_URL={base}\nANALYSIS_MODEL={model}\nANALYSIS_API_KEY={key}\n{extra}'
    path = tmp_path / '.env'
    path.write_text(content, encoding='utf-8')
    return path


def test_explicit_loader_defaults_disabled_and_does_not_modify_file(tmp_path):
    path = write_config(tmp_path)
    before = path.read_bytes()
    settings = load_analysis_settings(tmp_path)
    assert settings.enabled is False
    assert settings.data_use_confirmed is False
    assert settings.base_url == BASE_URL
    assert settings.model == MODEL
    assert settings.api_key == KEY
    assert settings.input_mode == 'real'
    assert settings.timeout_seconds == 120
    assert settings.protocol_verified is True
    assert settings.real_integration_authorized is False
    assert settings.purpose_authorized is False
    assert settings.transmission_authorized is False
    assert KEY not in repr(settings)
    assert path.read_bytes() == before


def test_one_time_enabled_and_authorization_flags_do_not_mutate_config(tmp_path):
    path = write_config(tmp_path, extra='ANALYSIS_ENABLED=false\nANALYSIS_DATA_USE_CONFIRMED=false\n')
    before = path.read_bytes()
    settings = load_analysis_settings(tmp_path, input_mode='synthetic', enable_once=True,
                                      one_request_authorized=True, purpose_authorized=True,
                                      transmission_authorized=True)
    assert settings.input_mode == 'synthetic' and settings.enabled is True
    assert settings.real_integration_authorized is True
    assert settings.purpose_authorized is True and settings.transmission_authorized is True
    # A synthetic or one-shot invocation never turns pending data usage to true.
    assert settings.data_use_confirmed is False
    assert path.read_bytes() == before


def test_explicit_persistent_switches_are_preserved(tmp_path):
    write_config(tmp_path, extra='ANALYSIS_ENABLED=true\nANALYSIS_DATA_USE_CONFIRMED=true\n')
    settings = load_analysis_settings(tmp_path)
    assert settings.enabled is True and settings.data_use_confirmed is True
    assert settings.real_integration_authorized is False


@pytest.mark.parametrize('quote', ["'", '"'])
def test_single_line_quotes_and_comments_are_supported_without_interpolation(tmp_path, quote):
    write_config(tmp_path, base=quote + BASE_URL + quote, model=quote + MODEL + quote,
                 key=quote + '${ARTIFICIAL_ANALYSIS_API_KEY}' + quote + ' # local comment',
                 extra='  ANALYSIS_ENABLED = "false"  # disabled\n')
    settings = load_analysis_settings(tmp_path)
    assert settings.api_key == '${ARTIFICIAL_ANALYSIS_API_KEY}'
    assert settings.enabled is False


def test_unknown_nonanalysis_lines_and_aa_key_never_supply_analysis_key(tmp_path, monkeypatch, capsys):
    write_config(tmp_path, key='', extra='ARTIFICIAL_ANALYSIS_API_KEY=SYNTHETIC-AA-SECRET\n'
                 'UNRELATED="broken source text\nnot even an assignment\n')
    monkeypatch.setenv('ANALYSIS_API_KEY', 'SYNTHETIC-GLOBAL-SECRET')
    monkeypatch.setenv('ARTIFICIAL_ANALYSIS_API_KEY', 'SYNTHETIC-GLOBAL-AA-SECRET')
    settings = load_analysis_settings(tmp_path)
    assert settings.api_key == ''
    captured = capsys.readouterr()
    assert not captured.out and not captured.err
    assert 'SYNTHETIC-AA-SECRET' not in repr(settings)


def test_missing_file_does_not_fall_back_to_global_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('ANALYSIS_BASE_URL', BASE_URL)
    monkeypatch.setenv('ANALYSIS_MODEL', MODEL)
    monkeypatch.setenv('ANALYSIS_API_KEY', KEY)
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'missing_config'
    assert KEY not in str(error.value)
    assert not (tmp_path / '.env').exists()


@pytest.mark.parametrize('field', ['ANALYSIS_BASE_URL', 'ANALYSIS_MODEL'])
def test_required_nonsecret_fields_do_not_use_implicit_defaults(tmp_path, field):
    path = write_config(tmp_path)
    content = path.read_text(encoding='utf-8')
    path.write_text('\n'.join(line for line in content.splitlines() if not line.startswith(field + '=')),
                    encoding='utf-8')
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'config_missing'


@pytest.mark.parametrize('suffix', [
    'ANALYSIS_API_KEY=SYNTHETIC-DUPLICATE\n',
    'ANALYSIS_ENABLED=false\nANALYSIS_ENABLED=true\n',
    'ANALYSIS_BASE_URL\n',
    'ANALYSIS_ENABLED true\n',
    'ANALYSIS_UNKNOWN=SYNTHETIC-SECRET\n',
    'export ANALYSIS_API_KEY=SYNTHETIC-SECRET\n',
])
def test_duplicate_malformed_and_unknown_analysis_keys_fail_fixed(tmp_path, suffix):
    write_config(tmp_path, extra=suffix)
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'invalid_config'
    assert 'SYNTHETIC' not in str(error.value)


@pytest.mark.parametrize('value', ['yes', '1', 'on', 'truthy', '${ENABLED}', ''])
def test_boolean_values_must_be_explicit_true_or_false(tmp_path, value):
    write_config(tmp_path, extra=f'ANALYSIS_ENABLED={value}\n')
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'invalid_config'


@pytest.mark.parametrize('base', [
    'https://hk.modex-ai.cloud', 'https://hk.modex-ai.cloud/v1/',
    'https://hk.modex-ai.cloud/v1/v1', 'https://hk.modex-ai.cloud/v1/chat/completions',
    'http://hk.modex-ai.cloud/v1', 'https://other.invalid/v1',
    'https://SYNTHETIC-SECRET@hk.modex-ai.cloud/v1',
    'https://hk.modex-ai.cloud/v1?secret=SYNTHETIC-SECRET',
    'https://hk.modex-ai.cloud/v1#SYNTHETIC-SECRET',
])
def test_service_url_must_exactly_match_selected_base(tmp_path, base):
    write_config(tmp_path, base=base)
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'service_mismatch'
    assert base not in str(error.value) and 'SYNTHETIC-SECRET' not in str(error.value)


@pytest.mark.parametrize('model', ['gpt-5.6', 'gpt-5.6-sol-new', 'SYNTHETIC-SECRET'])
def test_model_is_never_rewritten_or_switched(tmp_path, model):
    write_config(tmp_path, model=model)
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'model_mismatch'
    assert model not in str(error.value)


@pytest.mark.parametrize('key', ['contains space', '包含中文', 'x' * 2049, 'a\tb', 'a\x00b', 'a\x7fb'])
def test_api_key_rejects_nonvisible_ascii_and_oversize_without_echo(tmp_path, key):
    write_config(tmp_path, key="'" + key + "'")
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'invalid_api_key'
    assert key not in str(error.value)


@pytest.mark.parametrize('key', ['"SYNTHETIC-SECRET\nHeader: injected"',
                                '"SYNTHETIC-SECRET\rHeader: injected"',
                                '"SYNTHETIC-SECRET\r\nHeader: injected"',
                                '"SYNTHETIC-SECRET', "'SYNTHETIC-SECRET'junk"])
def test_unterminated_or_multiline_quoted_secrets_are_rejected(tmp_path, key):
    write_config(tmp_path, key=key)
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'invalid_config'
    assert 'SYNTHETIC-SECRET' not in str(error.value)


@pytest.mark.parametrize('arguments', [
    {'input_mode': 'other'}, {'input_mode': None}, {'enable_once': 'true'},
    {'one_request_authorized': 1}, {'purpose_authorized': 'true'}, {'transmission_authorized': []},
])
def test_call_arguments_fail_closed_before_file_access(tmp_path, monkeypatch, arguments):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid invocation must fail before any file read')

    monkeypatch.setattr(Path, 'open', forbidden)
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path, **arguments)
    assert error.value.code == 'invalid_arguments'


def test_utf8_bom_is_allowed_and_key_length_boundary_is_accepted(tmp_path):
    path = write_config(tmp_path, key='x' * 2048)
    content = path.read_text(encoding='utf-8')
    path.write_text(content, encoding='utf-8-sig')
    assert len(load_analysis_settings(tmp_path).api_key) == 2048


def test_loader_never_reads_environment_or_any_other_file(tmp_path, monkeypatch):
    import os
    path = write_config(tmp_path)
    original = Path.open
    opened = []

    def watch(self, *args, **kwargs):
        assert self == path
        opened.append(self)
        return original(self, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail('Environment fallback is forbidden')

    monkeypatch.setattr(Path, 'open', watch)
    monkeypatch.setattr(os, 'getenv', forbidden)
    assert load_analysis_settings(tmp_path).api_key == KEY
    assert opened == [path]


def test_decode_and_io_errors_do_not_retain_original_exception_content(tmp_path, monkeypatch):
    write_config(tmp_path)

    def broken(*args, **kwargs):
        raise OSError('SYNTHETIC-SECRET may not enter exception context')

    monkeypatch.setattr(Path, 'open', broken)
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'config_unreadable'
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert 'SYNTHETIC-SECRET' not in repr(error.value)


def test_invalid_utf8_is_fixed_error_without_original_decoder_context(tmp_path):
    path = write_config(tmp_path)
    with path.open('ab') as handle:
        handle.write(b'ANALYSIS_ENABLED=SYNTHETIC-SECRET\xff\n')
    with pytest.raises(AnalysisConfigError) as error:
        load_analysis_settings(tmp_path)
    assert error.value.code == 'config_unreadable'
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert 'SYNTHETIC-SECRET' not in repr(error.value)


def test_blank_quoted_key_remains_unconfigured_and_literal_hash_is_preserved(tmp_path):
    write_config(tmp_path, key='""')
    assert load_analysis_settings(tmp_path).api_key == ''
    write_config(tmp_path, key='SYNTHETIC#LITERAL')
    assert load_analysis_settings(tmp_path).api_key == 'SYNTHETIC#LITERAL'


def test_global_enable_and_data_use_never_override_absent_local_switches(tmp_path, monkeypatch):
    write_config(tmp_path)
    monkeypatch.setenv('ANALYSIS_ENABLED', 'true')
    monkeypatch.setenv('ANALYSIS_DATA_USE_CONFIRMED', 'true')
    settings = load_analysis_settings(tmp_path)
    assert settings.enabled is False and settings.data_use_confirmed is False


def test_error_constructor_never_accepts_secret_message_or_code():
    error = AnalysisConfigError('SYNTHETIC-SECRET')
    assert error.code == 'invalid_config'
    assert 'SYNTHETIC-SECRET' not in repr(error)
