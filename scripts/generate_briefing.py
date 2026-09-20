"""Explicit one-request Modex entry. Default/check perform no private I/O."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark_dashboard.briefing import AnalysisSettings, read_state


def _preflight() -> None:
    print(read_state(None, AnalysisSettings())['message'])
    print('离线预检查：Modex 非流式 Chat Completions 适配已准备，真实联调未授权。')
    print('目标：https://hk.modex-ai.cloud/v1/chat/completions；请求模型：gpt-5.6-sol。')
    print('未读取 .env、数据库或缓存，未调用分析服务；实际权限与协议兼容性待单次联调。')


def _write_review_report(text: str) -> None:
    destination = ROOT / 'reports' / 'M2C_first_real_briefing.md'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.m2c-report-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _run_m2c(args) -> int:
    """Fixed M2C scope: no persistent enable switch or second request permission."""
    if not all((args.real_once, args.allow_one_request, args.enable_once,
                args.confirm_purpose, args.confirm_transmission, args.confirm_m2c_scope, args.save_local)):
        print('M2C 需要本次限定用途、单次请求和本地保存展示的显式参数；未读取配置或发起请求。')
        return 2
    if args.synthetic_once or args.save_synthetic or args.check:
        print('M2C 只使用所选真实数据；不追加合成联调。未读取配置或发起请求。')
        return 2
    from benchmark_dashboard.analysis_config import load_analysis_settings
    from benchmark_dashboard.m2c_run import run_once, scoped_settings, render_markdown
    from benchmark_dashboard.modex_client import ModexClient, request_body_size
    from benchmark_dashboard.selected_facts import build_selected_fact_pack
    from benchmark_dashboard.selection import choose_acceptance_ids, validate_selection
    from benchmark_dashboard.store import read_dashboard
    try:
        settings = load_analysis_settings(ROOT, input_mode='real', enable_once=True,
            one_request_authorized=True, purpose_authorized=True, transmission_authorized=True)
        settings = scoped_settings(settings, confirmed_scope=True, retention_approved=True)
        state = read_dashboard(ROOT / 'data' / 'dashboard.sqlite3')
        selected_ids = validate_selection(state, args.models if args.models is not None else choose_acceptance_ids(state))
        pack = build_selected_fact_pack(state, selected_ids)
        body_bytes = request_body_size(pack, settings)
    except Exception:
        print('M2C 本地配置、选择或事实预检查失败；未发起请求，不输出配置或源数据原文。')
        return 2
    client = ModexClient(settings)
    try:
        view = run_once(state, selected_ids, settings, client, root=ROOT,
            confirmed_scope=True, retention_approved=True, request_body_bytes=body_bytes,
            selection_method='explicit_ids' if args.models is not None else 'technical_acceptance_fallback')
    except Exception:
        print('M2C 执行未完成，未发布成功结果；不得删除尝试记录或补发请求。')
        return 1
    finally:
        client.close()
    allowed = ('status', 'message', 'error_code', 'http_status', 'elapsed_seconds',
               'request_model', 'response_model', 'usage', 'cost', 'persisted',
               'request_reserved', 'scope_id', 'request_body_bytes', 'artifact_path', 'attempt_path')
    print(json.dumps({key: str(view.get(key)) if isinstance(view.get(key), Path) else view.get(key)
                      for key in allowed}, ensure_ascii=False, allow_nan=False))
    artifact = view.get('artifact')
    try:
        if artifact is not None:
            _write_review_report(render_markdown(artifact))
        elif view.get('request_reserved'):
            _write_review_report('# M2C 首份真实简报：未生成可用正文\n\n'
                                 '本次调用或校验失败，没有已校验成功简报；未补发请求。\n\n'
                                 + view['message'] + '\n')
    except Exception:
        print('本地 Markdown 保存未完成；同次已校验输出如已保存，可离线恢复，不再次请求。')
        return 1
    if view.get('status') == 'generated' and artifact is not None:
        print('本次完整正文及必要依据已保存至 reports/M2C_first_real_briefing.md；用户人工内容复核待完成。')
    else:
        print('本次未作为当前成功简报发布；无自动重试或模型切换。')
    return 0 if view.get('status') == 'generated' else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Modex 离线检查或经明确授权的一次生成。')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='离线检查；不读取配置、数据库或缓存')
    modes.add_argument('--synthetic-once', action='store_true', help='合成输入、真实服务联调一次；不读取业务数据库')
    modes.add_argument('--real-once', action='store_true', help='现有真实最小事实分析一次；需独立用途与传输确认')
    parser.add_argument('--allow-one-request', action='store_true', help='已获本次最多一个生成 POST 的明确授权')
    parser.add_argument('--enable-once', action='store_true', help='只在本次命令启用；不修改配置或常驻页面')
    parser.add_argument('--confirm-purpose', action='store_true', help='确认有真实依据的分析用途')
    parser.add_argument('--confirm-transmission', action='store_true', help='确认真实最小事实发往 Modex 的范围')
    parser.add_argument('--save-synthetic', action='store_true', help='仅将合成联调结果保存到隔离目录供本机复核')
    parser.add_argument('--show-result', action='store_true', help='在本机终端显示通过校验的三部分结果')
    parser.add_argument('--m2c', action='store_true', help='限定 M2C 首份真实简报；固定持久尝试记录防重复')
    parser.add_argument('--confirm-m2c-scope', action='store_true', help='本次所选2–4条必要数据发送与本地复核用途已确认')
    parser.add_argument('--save-local', action='store_true', help='本次完整简报及必要依据允许本机保存和只读展示')
    parser.add_argument('--models', nargs='+', help='明确的2–4个稳定ID；M2C省略时使用技术验收样例')
    args = parser.parse_args(argv)
    if args.m2c:
        return _run_m2c(args)
    if args.models is not None or args.confirm_m2c_scope or args.save_local:
        print('模型选择和本次本地保存参数仅适用于明确的 M2C 路径；未读取配置或发起请求。')
        return 2
    if not (args.synthetic_once or args.real_once):
        _preflight()
        return 0 if args.check else 2
    if not args.allow_one_request:
        print('真实联调未授权；需明确允许本次最多一个生成 POST。未读取配置或数据库。')
        return 2
    if ((args.save_synthetic and not args.synthetic_once)
            or (args.synthetic_once and (args.confirm_purpose or args.confirm_transmission))):
        print('合成与真实模式不可混用；未读取配置或数据库。')
        return 2
    if args.real_once and not (args.confirm_purpose and args.confirm_transmission):
        print('真实数据分析用途与发往 Modex 的范围尚未明确确认。未读取配置或数据库。')
        return 2

    # Only this explicitly authorised branch may open local analysis settings.
    from benchmark_dashboard.analysis_config import AnalysisConfigError, load_analysis_settings
    from benchmark_dashboard.briefing import check_settings, generate_briefing
    from benchmark_dashboard.modex_client import ModexClient
    try:
        settings = load_analysis_settings(
            ROOT, input_mode='synthetic' if args.synthetic_once else 'real',
            enable_once=args.enable_once, one_request_authorized=args.allow_one_request,
            purpose_authorized=args.confirm_purpose,
            transmission_authorized=args.confirm_transmission,
        )
    except AnalysisConfigError:
        print('分析配置缺失或格式不符；未发起请求。请在本机核对分析配置。')
        return 2
    gate = check_settings(settings)
    if gate:
        print(gate[1])
        return 2
    try:
        if args.synthetic_once:
            from benchmark_dashboard.synthetic_briefing import build_synthetic_pack
            pack = build_synthetic_pack()
            cache = ROOT / '.cache' / 'modex-integration' if args.save_synthetic else None
        else:
            from benchmark_dashboard.fact_pack import build_fact_pack
            from benchmark_dashboard.store import read_dashboard
            pack = build_fact_pack(read_dashboard(ROOT / 'data' / 'dashboard.sqlite3'))
            # Real retention scope is pending: no implicit permanent result file.
            cache = None
    except Exception:
        print('本地事实整理失败；未发起请求。原数据库保持不变。')
        return 1

    client = None
    try:
        client = ModexClient(settings)
        view = generate_briefing(pack, settings, client, cache_dir=cache, force_refresh=True)
    except Exception:
        print('生成入口失败，未发布结果；不自动重试。请另行核对配置或接口契约。')
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                # Never turn an internal cleanup exception into a raw traceback.
                pass

    print('合成输入、真实服务联调' if args.synthetic_once else '现有评测最小事实、真实服务分析')
    # Only fixed/validated metadata, never headers, raw exceptions or responses.
    allowed = ('status', 'message', 'error_code', 'http_status', 'elapsed_seconds',
               'request_model', 'response_model', 'usage', 'cost', 'persisted')
    print(json.dumps({key: view.get(key) for key in allowed}, ensure_ascii=False, allow_nan=False))
    print('本次无自动重试；超时不代表未产生费用。人工语义复核尚未完成。')
    if view['status'] == 'generated' and args.show_result:
        print(json.dumps(view['result'], ensure_ascii=False, indent=2))
    if args.real_once:
        print('真实结果仅保留于本次内存；未写入真实缓存或接入网页展示。')
    elif args.save_synthetic:
        print('隔离联调目录：.cache/modex-integration；不属于真实简报缓存。TTL 不会物理删除文件。')
    return 0 if view['status'] == 'generated' else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
