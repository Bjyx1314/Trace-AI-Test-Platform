#!/usr/bin/env python
"""一键更新部署：本地打包平台代码 → 上传服务器 → docker 重建容器。

用法:
    python deploy/update.py
    (服务器密码从环境变量 DEPLOY_PWD 读取，没有则提示输入)

特点:
- 只更新平台代码(backend/frontend/compose)，【不动】服务器上的 backend/.env(打包时已排除)
- docker 层缓存：requirements 不变时不会重下 chromium，代码更新很快
可用环境变量覆盖：DEPLOY_HOST / DEPLOY_PORT / DEPLOY_USER / DEPLOY_PWD
"""
import io
import os
import sys
import tarfile
import time
import getpass

try:
    import paramiko
except ImportError:
    print("缺少 paramiko，请先安装:  pip install paramiko")
    sys.exit(1)

HOST = os.environ.get("DEPLOY_HOST")
PORT = int(os.environ.get("DEPLOY_PORT", "22"))
USER = os.environ.get("DEPLOY_USER", "deploy")
PWD = os.environ.get("DEPLOY_PWD")
REMOTE_DIR = os.environ.get("DEPLOY_REMOTE_DIR", "/opt/ai-test-platform")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE = ["backend", "frontend", "docker-compose.prod.yml"]
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
                "uploads", "login_states", "login_tmp", ".pytest_cache", ".idea", ".vscode"}
EXCLUDE_NAMES = {".env"}  # 绝不覆盖服务器上的 .env(含密钥/真实配置)


def _filter(ti: tarfile.TarInfo):
    parts = ti.name.replace("\\", "/").split("/")
    if any(p in EXCLUDE_DIRS for p in parts):
        return None
    if os.path.basename(ti.name) in EXCLUDE_NAMES:
        return None
    return ti


def main():
    if not HOST:
        print("请先设置 DEPLOY_HOST；可选设置 DEPLOY_PORT/DEPLOY_USER/DEPLOY_REMOTE_DIR。")
        sys.exit(2)
    password = PWD or getpass.getpass(f"{USER}@{HOST} 服务器密码: ")
    print("[1/4] 打包平台代码(排除 node_modules/.env 等)...")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in INCLUDE:
            tar.add(os.path.join(ROOT, item), arcname=item, filter=_filter)
    data = buf.getvalue()
    print(f"      包大小 {len(data) / 1024 / 1024:.1f} MB")

    print(f"[2/4] 连接 {USER}@{HOST}:{PORT} 并上传...")
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER, password=password, timeout=20)
    sftp = cli.open_sftp()
    with sftp.open("/tmp/platform_update.tgz", "wb") as rf:
        rf.write(data)
    script = (
        "set -e\n"
        f"cd {REMOTE_DIR}\n"
        "echo '[3/4] 解包(保留 backend/.env)...'\n"
        f"tar -xzf /tmp/platform_update.tgz -C {REMOTE_DIR}\n"
        "echo '[4/4] 重建并启动(layer 缓存命中时很快)...'\n"
        "docker compose -f docker-compose.prod.yml up -d --build\n"
        "sleep 5\n"
        "docker compose -f docker-compose.prod.yml ps\n"
        "docker image prune -f >/dev/null 2>&1 || true\n"
        "echo '=== 更新完成 ==='\n"
    )
    with sftp.open("/tmp/_update.sh", "w") as f:
        f.write(script)
    sftp.close()

    chan = cli.get_transport().open_session()
    chan.settimeout(1800)
    chan.exec_command("bash /tmp/_update.sh 2>&1")
    start = time.time()
    while True:
        if chan.recv_ready():
            sys.stdout.write(chan.recv(8192).decode(errors="ignore"))
            sys.stdout.flush()
        elif chan.exit_status_ready():
            break
        elif time.time() - start > 1800:
            print("\n[超时]")
            break
        else:
            time.sleep(0.2)
    code = chan.recv_exit_status()
    cli.close()
    print(f"\n[结束 exit={code}]  访问: http://{HOST}/")
    sys.exit(0 if code == 0 else 1)


if __name__ == "__main__":
    main()
