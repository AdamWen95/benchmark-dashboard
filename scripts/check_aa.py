"""Run from the project root using the project virtual environment."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_dashboard.acquisition import acquire, save_last_check, utc_now
from benchmark_dashboard.client import SourceError
from benchmark_dashboard.config import SafeError
from benchmark_dashboard.reporting import write_m0_failure, write_m0_success
from benchmark_dashboard.validation import DataValidationError


def safe_failure_report(started_at: str, message: str) -> None:
    try:
        write_m0_failure(ROOT, started_at, message)
    except Exception:
        print('失败报告无法保存，请检查目录权限和可用空间。')


def main() -> int:
    started_at = utc_now()
    try:
        result = acquire(ROOT, started_at=started_at)
        write_m0_success(ROOT, result)
        save_last_check(ROOT, result)
    except (SourceError, SafeError, DataValidationError) as error:
        safe_failure_report(started_at, str(error))
        print(f'M0 未通过：{error}')
        return 1
    except Exception:
        safe_failure_report(started_at, '本地保存或处理失败，请检查目录权限与可用空间。')
        print('M0 未通过：本地保存或处理失败。详情不输出，以保护本地配置。')
        return 1
    print(f'M0 通过：HTTP {result.http_status}，{len(result.valid.records)} 条模型记录。')
    print(f'采集时间（UTC）：{result.collected_at}')
    print(f'未知字段 {len(result.valid.unknown_fields)} 个；报告：reports/M0_data_source_check.md')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
