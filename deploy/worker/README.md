# App 真机执行机 worker

把 App 真机执行放到「插着真机的执行机」本地完成（USB 直连、AI 视觉、稳），主动连平台领任务、回传结果。
**Windows 与 macOS 均支持**（同一份 worker.py，adb 解析、开机自启按系统自适应）。未来可平滑演进为 Sonic-agent。

## 执行机要求
- 安卓真机：开「开发者模式 + USB 调试」，USB 接到执行机，授权调试。
- 装 **adb**（Android platform-tools），`adb devices` 能看到设备（status=device）。exe/二进制版已内置 adb，可免装。
- 装 **Python 3.10+**（仅脚本方式需要；下载打包版免装）。
- 能访问平台地址（如 `http://localhost:8000`）和配置的 AI 服务地址。

## 推荐：从平台「连接我的真机」下载打包版（免装环境）
平台执行弹框 → 移动端「连接真机」→「连接我的真机」。页面会**按你所在系统**给出对应的执行助手与首次启动命令：
- **Windows**：下载 `tp-worker.exe`，在其所在目录 PowerShell 粘贴运行页面给出的命令。
- **macOS**：下载 `tp-worker`，在其所在目录「终端」粘贴运行页面给出的命令（首次会 `chmod +x`）。
  首次运行若被 Gatekeeper 拦截，到「系统设置 → 隐私与安全性」点“仍要打开”。

首次成功后会记住配置并**设为开机自启**（Windows 走注册表 Run，macOS 走用户级 LaunchAgent），以后开机自动上线。

## 打包命令（管理员构建产物，放到平台服务器）
- Windows：`powershell -ExecutionPolicy Bypass -File deploy\worker\build-exe.ps1` → `dist\tp-worker.exe`
- macOS：`bash deploy/worker/build-mac.sh` → `dist/tp-worker`（需在 Mac 上执行）

产物分别放到后端配置 `WORKER_EXE_PATH`（Windows）与 `WORKER_EXE_PATH_MAC`（macOS）指向的位置，
`/api/worker/download?os=mac|win` 会按客户端系统下发对应产物。

## 脚本方式启动（源码运行，需本机 Python + adb）
Windows：
```powershell
powershell -ExecutionPolicy Bypass -File deploy\worker\install-worker.ps1
```
它会：装依赖 → 首次提示粘贴一次 `WORKER_TOKEN`（找管理员要，写进 backend\.env）→ 启动 worker。

macOS / Linux：
```bash
python3 -m pip install -r deploy/worker/requirements-worker.txt
# 在 backend/.env 里写入 WORKER_TOKEN=xxx（找管理员要）
python3 deploy/worker/worker.py
```
**AI 配置无需手填**：worker 启动时自动读取本机 `backend/.env` 里的 `AI_*`（复用平台同一套 AI），
`WORKER_ID` 默认取机器名，`PLATFORM_URL` 默认指向 `http://localhost:8000`。
（首次 uiautomator2 会给手机装 atx-agent，保持 USB 连接、手机点允许。）

## 零配置原理 & 可选覆盖
默认全自动；只在需要时设这些环境变量覆盖：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AI_PROVIDER/AI_API_KEY/AI_BASE_URL/AI_MODEL` | **自动**读 backend/.env | 各执行机用各自本地 AI 配置 |
| `WORKER_TOKEN` | 读 backend/.env | 平台 worker 令牌（首次由安装脚本写入） |
| `WORKER_ID` | **机器名** hostname | 多执行机自然不同 |
| `PLATFORM_URL` | `http://localhost:8000` | 平台地址 |
| `WORKER_NAME` | =WORKER_ID | 显示名 |
| `WORKER_SHARED` | `false` | `true`=本机设备作公共/默认设备，没装 worker 的人兜底走它 |

启动后每 10s 心跳上报设备；平台「执行配置」里就能看到这台设备，App 用例执行会派发到它本地跑。
启动横幅会打印它取到的 AI provider/model 与是否拿到 Key，一眼可见是否自动配好。

## 路由规则（平台侧）
1. 执行时**显式选了目标设备** → 跑那台；
2. 没选 → 走标了 `WORKER_SHARED=true` 的**公共默认设备**兜底；
3. 没有任何在线设备 → 执行直接报 `env_error`（不空等）。
