"""Explicit manual synchronization. No web page invokes this script."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark_dashboard.acquisition import Acquisition, acquire, utc_now
from benchmark_dashboard.client import API_URL, SourceError
from benchmark_dashboard.config import SafeError
from benchmark_dashboard.store import record_failure, record_success
from benchmark_dashboard.validation import DataValidationError, validate_payload


def from_m0(root: Path) -> Acquisition:
    """Import the verified real M0 snapshot without making another HTTP request."""
    try:
        metadata = json.loads((root / 'data' / 'last_check.json').read_text(encoding='utf-8'))
        digest = metadata['content_hash']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError
        path = root / 'data' / 'snapshots' / f'{digest}.json'
        envelope = json.loads(path.read_text(encoding='utf-8'))
        if envelope['source'] != 'artificial_analysis' or envelope['endpoint'] != API_URL:
            raise ValueError
        valid = validate_payload(envelope['payload'])
        if valid.content_hash != digest or envelope['content_hash'] != digest:
            raise ValueError
        return Acquisition(valid, metadata['started_at'], metadata['collected_at'],
                           metadata['http_status'], metadata['attempts'], path)
    except (OSError, ValueError, KeyError, TypeError, DataValidationError):
        raise SafeError('invalid_m0_snapshot', '未找到可核验的 M0 快照；请先运行 scripts/check_aa.py。') from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='手动采集 Artificial Analysis 并事务保存到本地 SQLite。')
    parser.add_argument('--from-m0', action='store_true', help='复用已校验的真实 M0 快照，不请求 API')
    args = parser.parse_args(argv)
    started_at = utc_now()
    database = ROOT / 'data' / 'dashboard.sqlite3'
    try:
        result = from_m0(ROOT) if args.from_m0 else acquire(ROOT, started_at=started_at)
        saved = record_success(database, result)
    except (SourceError, SafeError, DataValidationError) as error:
        message = str(error)
        code = getattr(error, 'code', 'invalid_schema')
        try:
            record_failure(database, started_at=started_at, finished_at=utc_now(), error_code=code,
                           message=message, http_status=getattr(error, 'http_status', None),
                           attempts=getattr(error, 'attempts', 0))
        except Exception:
            print('失败运行记录无法保存，请检查目录权限和可用空间。')
        print(f'采集失败：{message} 上一次有效数据保持可用。')
        return 1
    except Exception:
        try:
            record_failure(database, started_at=started_at, finished_at=utc_now(), error_code='local_error',
                           message='本地保存或处理失败，请检查目录权限和可用空间。')
        except Exception:
            pass
        print('采集失败：本地保存或处理失败。详情不输出，以保护本地配置。')
        return 1
    action = '导入 M0 已校验快照' if args.from_m0 else '真实 API 采集'
    version = '新增数据版本' if saved['new_snapshot'] else '复用相同内容版本'
    print(f'{action}成功：{len(result.valid.records)} 条模型记录，{version}，运行编号 {saved["run_id"]}。')
    print(f'源数据实际采集时间（UTC）：{result.collected_at}')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
