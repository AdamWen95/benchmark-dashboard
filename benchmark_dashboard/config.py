"""Read the local API configuration without exposing credentials."""

from pathlib import Path

from dotenv import dotenv_values


class SafeError(Exception):
    """An error whose message contains no configuration values."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def load_api_key(root: Path) -> str:
    """Read only ``root/.env``; never fall back to the process environment."""
    config_path = Path(root) / ".env"
    try:
        if not config_path.is_file():
            raise SafeError("missing_config", "项目根目录缺少 .env 配置文件。")
        # python-dotenv's parser warnings report line numbers only, never the
        # original configuration text. utf-8-sig also accepts Windows BOM files.
        values = dotenv_values(config_path, interpolate=False, encoding="utf-8-sig")
    except SafeError:
        raise
    except (OSError, UnicodeError):
        raise SafeError("unreadable_config", "无法读取本地 .env 配置，请检查文件权限和 UTF-8 编码。") from None

    key = values.get("ARTIFICIAL_ANALYSIS_API_KEY")
    if not isinstance(key, str) or not key:
        raise SafeError("missing_api_key", "请在本机 .env 中填写 ARTIFICIAL_ANALYSIS_API_KEY。")
    # HTTP header credentials must not contain whitespace, control characters,
    # or non-ASCII text. Never include the rejected value in an exception.
    if len(key) > 4096 or any(not 33 <= ord(char) <= 126 for char in key):
        raise SafeError("invalid_api_key", "ARTIFICIAL_ANALYSIS_API_KEY 格式无效，请在本机检查配置。")
    return key
