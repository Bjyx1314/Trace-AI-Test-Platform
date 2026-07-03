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

# 基础工具(git 给框架仓库用，curl 健康检查，adb 供 Sonic 远程真机 adb connect)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates adb \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright chromium + 其系统依赖(PC web 执行/框架登录用)
RUN python -m playwright install --with-deps chromium

COPY . .

EXPOSE 8000
CMD ["sh", "entrypoint.sh"]
