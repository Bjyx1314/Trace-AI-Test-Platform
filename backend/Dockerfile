# 平台后端 + PC 自动化框架运行环境(playwright chromium)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

# Debian apt 换阿里云镜像(deb.debian.org 境内极慢)
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi

# 基础工具(git 给框架仓库用，curl 健康检查，adb 供 Sonic 远程真机 adb connect；
# libgl1/libglib2.0-0 供 OCR 用的 opencv 运行)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates adb libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# OCR 引擎(rapidocr-onnxruntime, App 自绘控件文字定位用)。单独一层且【失败不阻断构建】——
# 装不上就运行时降级为纯视觉(app_ocr 惰性加载, 缺引擎自动 no-op)。
RUN pip install --no-cache-dir rapidocr-onnxruntime || echo "OCR 安装失败, 将以纯视觉运行"

# Playwright chromium + 其系统依赖(平台自带 PC web 执行 web_agent_runner 用)
RUN python -m playwright install --with-deps chromium

# 系统 Google Chrome(自动化框架登录用 channel=chrome / /opt/google/chrome)。
# 装进镜像固化：否则它只在运行时安装、容器重建或被清理后就没了，导致外部框架登录失败。
RUN python -m playwright install --with-deps chrome

COPY . .

EXPOSE 8000
CMD ["sh", "entrypoint.sh"]
