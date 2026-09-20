"""Dedicated daily timer entry point. Default invocation is read-only preview."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description='每日 09:00 调度；默认仅预览。')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--preview', action='store_true')
    mode.add_argument('--check', action='store_true', help='只读本地配置和密钥校验，不联网')
    mode.add_argument('--execute', action='store_true', help='执行已授权的每日一次更新')
    args = parser.parse_args(argv)
    from benchmark_dashboard.daily_schedule import check_schedule, execute_scheduled, schedule_preview
    from benchmark_dashboard.daily_run import DailyError
    from benchmark_dashboard.config import SafeError
    from benchmark_dashboard.analysis_config import AnalysisConfigError
    try:
        result = execute_scheduled(ROOT) if args.execute else check_schedule(ROOT) if args.check else schedule_preview(ROOT)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result['status'] == 'failed' else 0
    except (DailyError, SafeError, AnalysisConfigError) as error:
        print(json.dumps({'status': 'blocked', 'message': str(error)}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({'status': 'failed', 'message': '每日调度检查或执行失败；未自动重试。请核查本地配置和持久运行记录。'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
