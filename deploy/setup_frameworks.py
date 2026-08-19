#!/usr/bin/env python
"""服务器框架初始化：用 git clone 把三个框架拉到服务器(替换旧的 tar 副本)。

两个 git 仓库(PC Web 与 移动端 共用 web_ui_automation)：
    web_ui_automation.git  -> /opt/framework   (PC Web ui_web + 移动端 ui_app)
    interfaceauto2.0.git   -> /opt/framework-inter      (接口 cases)

用法:
    set GIT_USER=your-user & set GIT_PWD=*** & set DEPLOY_PWD=*** & python deploy/setup_frameworks.py
    (未设环境变量则交互式输入；不在文件里写任何密码)

做的事(服务器上)：
  1) 对每个仓库：有 .git 就 git pull；否则删掉旧目录(tar 副本)后 git clone(带凭据，分支 master)
  2) web_ui_automation 的 projects.yaml 把 browser_channel 置空(用容器内置 chromium)
  3) 重启 backend 容器并跑 seed_frameworks 登记三条注册
凭据写进各仓库 .git/config 的 remote URL，后续 deploy/update.py 的 git pull 免再输入。
可用环境变量覆盖：DEPLOY_HOST / DEPLOY_PORT / DEPLOY_USER / DEPLOY_PWD / GIT_USER / GIT_PWD
"""
import getpass
import os
import sys
import time

try:
    import paramiko
except ImportError:
    print("缺少 paramiko，请先安装:  pip install paramiko")
    sys.exit(1)

HOST = os.environ.get("DEPLOY_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEPLOY_PORT", "222"))
USER = os.environ.get("DEPLOY_USER", "root")
PWD = os.environ.get("DEPLOY_PWD") or getpass.getpass(f"{USER}@{HOST} 服务器密码: ")

GIT_HOST = os.environ.get("GIT_HOST", "git.example.test")
GIT_USER = os.environ.get("GIT_USER", "your-user")
GIT_PWD = os.environ.get("GIT_PWD") or getpass.getpass(f"git({GIT_USER}@{GIT_HOST}) 密码: ")

REMOTE_DIR = "/opt/test-platform"
# (仓库路径, 目标目录, 分支)
REPOS = [
    (os.environ.get("WEB_FRAMEWORK_REPO", "example/web-automation.git"), "/opt/framework", os.environ.get("WEB_FRAMEWORK_BRANCH", "main")),
    (os.environ.get("API_FRAMEWORK_REPO", "example/api-automation.git"), "/opt/framework-inter", os.environ.get("API_FRAMEWORK_BRANCH", "main")),
]


def main():
    auth = f"{GIT_USER}:{GIT_PWD}@{GIT_HOST}"
    lines = ["set -e"]
    for path, dest, branch in REPOS:
        url = f"http://{auth}/{path}"
        lines += [
            f"echo '==== {dest} ===='",
            f"if [ -d {dest}/.git ]; then",
            f"  echo '已是 git 仓库，git pull...'; (cd {dest} && git remote set-url origin '{url}' && (git pull --ff-only || git pull))",
            f"else",
            f"  echo '非 git(或不存在)，删除旧副本后 clone...'; rm -rf {dest}; git clone -b {branch} '{url}' {dest}",
            f"fi",
        ]
    # web 框架用容器内置 chromium：projects.yaml 的 browser_channel 置空
    lines += [
        "YAML=/opt/framework/common/config/projects.yaml",
        "if [ -f $YAML ]; then sed -i -E 's/^([[:space:]]*browser_channel:).*/\\1 \"\"/' $YAML && echo 'projects.yaml browser_channel 已置空'; fi",
        f"cd {REMOTE_DIR}",
        "echo '重启 backend 使挂载/登记生效...'",
        "docker compose -f docker-compose.prod.yml up -d backend",
        "sleep 6",
        "docker compose -f docker-compose.prod.yml exec -T backend python -m app.seed_frameworks || true",
        "echo '==== 框架目录 ===='",
        "ls -d /opt/framework/ui_web /opt/framework/ui_app /opt/framework-inter/cases 2>&1 || true",
        "echo '=== 完成 ==='",
    ]
    script = "\n".join(lines) + "\n"

    print(f"[1/2] 连接 {USER}@{HOST}:{PORT}...")
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER, password=PWD, timeout=20)
    sftp = cli.open_sftp()
    with sftp.open("/tmp/_setup_fw.sh", "w") as f:
        f.write(script)
    sftp.close()

    print("[2/2] 在服务器执行 git clone/pull + 登记...")
    chan = cli.get_transport().open_session()
    chan.settimeout(1800)
    chan.exec_command("bash /tmp/_setup_fw.sh 2>&1")
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
    cli.exec_command("rm -f /tmp/_setup_fw.sh")
    cli.close()
    print(f"\n[结束 exit={code}]")
    sys.exit(0 if code == 0 else 1)


if __name__ == "__main__":
    main()
