"""Run from the user's own PowerShell to restart only this project's backend."""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
PORT = 3896

def find_server():
    listeners = {c.pid for c in psutil.net_connections(kind='tcp') if c.status == psutil.CONN_LISTEN and c.laddr.port == PORT and c.pid}
    if len(listeners) > 1:
        raise RuntimeError('后端端口有多个进程监听，已停止操作，请人工检查。')
    if not listeners:
        return None
    p = psutil.Process(listeners.pop())
    args = p.cmdline()
    if 'uvicorn' not in args or 'app.main:app' not in args or Path(p.cwd()).resolve() != BACKEND.resolve():
        raise RuntimeError(f'端口 {PORT} 被其他程序占用，已停止操作（PID {p.pid}）。')
    return p

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='只检查，不停止或启动服务')
    args = parser.parse_args()
    server = find_server()
    print('现有项目后端 PID：', server.pid if server else '未运行', flush=True)
    if args.check:
        return
    if server:
        server.terminate()
        server.wait(timeout=30)
    env = os.environ.copy()
    for name in ('DB_BACKEND','MYSQL_HOST','MYSQL_PORT','MYSQL_USER','MYSQL_PASSWORD','MYSQL_AUTH_DATABASE','MYSQL_DATABASE_PREFIX'):
        env.pop(name, None)
    temp = BACKEND / 'data' / 'runtime' / 'tmp'
    temp.mkdir(parents=True, exist_ok=True)
    for name in ('TEMP', 'TMP', 'TMPDIR'):
        env[name] = str(temp)
    env['WORDAGENT_RUNTIME_DIR'] = str(BACKEND / 'data' / 'runtime')
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf8'
    log = ROOT / '.cache' / 'service-start' / 'backend-user.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab') as output:
        child = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(PORT)], cwd=BACKEND, env=env, stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
    print('已启动后端 PID：', child.pid, '；日志：', log, flush=True)
    import urllib.request
    for _ in range(30):
        if child.poll() is not None:
            raise RuntimeError('后端进程已退出，请检查日志。')
        try:
            with urllib.request.urlopen('http://127.0.0.1:3896/api/health', timeout=2) as response:
                if response.status == 200:
                    print('后端健康检查通过，后台任务将尝试恢复。', flush=True)
                    return
        except Exception:
            time.sleep(1)
    print('后端已启动但健康检查尚未通过，请检查日志。', flush=True)

if __name__ == '__main__':
    main()
