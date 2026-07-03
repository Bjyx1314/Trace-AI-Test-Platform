#!/bin/sh
# 后端容器启动：等 DB 就绪 → 迁移 → 灌枚举(仅枚举，无 mock) → 起服务
set -e

echo "[entrypoint] 等待数据库就绪并执行迁移..."
n=0
until alembic upgrade head; do
  n=$((n+1))
  if [ "$n" -ge 30 ]; then echo "[entrypoint] 迁移重试超限，退出"; exit 1; fi
  echo "[entrypoint] DB 未就绪/迁移失败，5s 后重试($n)"; sleep 5
done

echo "[entrypoint] 灌入枚举数据(幂等)..."
python -m app.seed_enums || echo "[entrypoint] seed_enums 失败(忽略，可后续手动跑)"

echo "[entrypoint] 确保存在示例项目(无项目时自动建，幂等)..."
python -m app.seed_default_project || echo "[entrypoint] seed_default_project 失败(忽略)"

# 注意：不在此安装框架自身 requirements —— 框架会钉 urllib3/requests 等版本，
# 装进平台容器可能与平台依赖冲突，且拖慢每次启动。PC Web 执行用平台镜像内置的
# playwright 即可。接口框架若需独立依赖，后续单独处理(独立 venv/构建步骤)。

echo "[entrypoint] 登记框架仓库(幂等)..."
python -m app.seed_frameworks || echo "[entrypoint] seed_frameworks 失败(忽略)"

echo "[entrypoint] 回填账号为姓名拼音(幂等)..."
python -m app.backfill_usernames || echo "[entrypoint] backfill_usernames 失败(忽略)"

echo "[entrypoint] 启动 uvicorn:8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
