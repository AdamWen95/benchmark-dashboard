"""Loopback-only launcher that refuses an occupied port without killing services."""
import argparse
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='仅在 127.0.0.1 启动中文模型指标面板。')
    parser.add_argument('--port', type=int, default=8502, help='本机端口，默认 8502')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('端口必须在 1024–65535 之间。')
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(('127.0.0.1', args.port))
    except OSError:
        print(f'无法绑定本机端口 {args.port}：可能已被占用或没有权限。未终止任何服务。')
        print('可指定其他端口，例如：.\\.venv\\Scripts\\python.exe scripts/run_local.py --port 8503')
        return 1
    print(f'本地网页：http://127.0.0.1:{args.port}；按 Ctrl+C 停止。', flush=True)
    command = [sys.executable, '-m', 'streamlit', 'run', str(ROOT / 'app.py'),
               '--server.address=127.0.0.1', f'--server.port={args.port}', '--server.headless=true',
               '--browser.gatherUsageStats=false', '--server.fileWatcherType=none',
               '--client.showErrorDetails=none']
    try:
        return subprocess.call(command, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
