"""Manual daily entry point; the authorized timer uses run_scheduled_daily.py."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description='全量日更：默认离线预览；执行必须显式授权。')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--status', action='store_true', help='只读最近一次日更状态，不读取密钥')
    parser.add_argument('--date', help='配置时区的今天，YYYY-MM-DD')
    parser.add_argument('--scope-id', help='本次新授权标识，不能重用旧范围')
    parser.add_argument('--enable-once', action='store_true')
    parser.add_argument('--allow-one-analysis', action='store_true')
    parser.add_argument('--confirm-limited-use', action='store_true')
    parser.add_argument('--model-id', action='append', help='例证2–4条稳定ID；仅本机映射名称')
    args = parser.parse_args(argv)
    if sum((args.preview, args.execute, args.status)) > 1:
        parser.error('--preview、--execute、--status 只能选择一个')
    from benchmark_dashboard.daily_run import DailyError, execute_daily, preview_daily, read_latest_run
    from benchmark_dashboard.config import SafeError
    from benchmark_dashboard.analysis_config import AnalysisConfigError
    try:
        if args.status:
            result = read_latest_run(ROOT)
            value = {'status': 'no_saved_daily_run'} if result is None else {
                'run_id': result['run_id'], 'collection': result['collection'],
                'analysis_status': result['analysis']['status'], 'analysis_attempts': result['analysis']['attempts'],
                'result_path': result['result_path'], 'finished_at': result['finished_at']}
            print(json.dumps(value, ensure_ascii=False))
            return 0
        if not args.execute:
            result = preview_daily(ROOT, ids=args.model_id)
            overview, pack = result['overview'], result['fact_pack']
            print(json.dumps({'mode': result['mode'], 'notice': result['notice'],
                              'network_requests': 0, 'total_records': overview['total_records'],
                              'changes': overview['changes']['message'],
                              'selected_examples': pack['scope']['selected_count'],
                              'fact_bytes': len(json.dumps(pack, ensure_ascii=False).encode('utf-8'))}, ensure_ascii=False))
            return 0
        if not args.date or not args.scope_id:
            parser.error('真实执行需要 --date 和 --scope-id')
        result = execute_daily(ROOT, run_date=args.date, scope_id=args.scope_id,
                               enable_once=args.enable_once, allow_analysis=args.allow_one_analysis,
                               confirm_limited_use=args.confirm_limited_use, ids=args.model_id)
        print(json.dumps({'run_id': result['run_id'], 'collection': result['collection'],
                          'analysis_status': result['analysis']['status'],
                          'analysis_attempts': result['analysis']['attempts'],
                          'total_records': result['overview']['total_records']}, ensure_ascii=False))
        return 0 if result['collection']['status'] == 'success' and result['analysis']['status'] in ('generated', 'disabled') else 1
    except (DailyError, SafeError, AnalysisConfigError) as error:
        print(str(error))
        return 1
    except Exception:
        print('日更预检查未通过或本轮已被占用；未自动重试。请核对本地非秘密配置与持久运行记录。')
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
