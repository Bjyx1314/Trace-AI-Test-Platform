# 配置说明

配置通过根目录 `.env`、`backend/.env` 和系统管理页面提供。模板中的管理员默认凭据只用于首次本地体验。

## 必填安全配置

| 变量 | 说明 |
|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL 密码 |
| `JWT_SECRET` | 平台 JWT 签名密钥，至少 32 个随机字符 |
| `DEFAULT_ADMIN_PASSWORD` | 首次启动创建的管理员密码，默认 `admin`，非本地环境必须覆盖 |

默认管理员为 `admin` / `admin`。首次创建完成后，修改环境变量不会覆盖已有账号或重置其密码。

## AI

`AI_PROVIDER` 支持项目代码中实现的 Provider，`AI_MODEL` 必须显式填写，平台不内置默认模型。使用官方服务时设置对应 API Key，自建兼容网关可设置 `AI_BASE_URL`。AI 未配置时，真实执行模式会明确报环境错误，不会伪造通过。

## 外部系统

- 飞书：设置 `FEISHU_APP_*` 与表格 ID；不用时留空。
- 外部 SSO/任务系统：设置 `EXTERNAL_TASK_API_URL` 和可选 API Key；不用时留空，平台走本地登录。
- Sonic：设置 `SONIC_ENABLED=true`、网关地址和凭据。
- 外部框架：设置 `FRAMEWORK_WEB_GIT_URL`、`FRAMEWORK_API_GIT_URL`、挂载路径和可选 `FRAMEWORK_PLATFORM_MAP_JSON`。

`FRAMEWORK_PLATFORM_MAP_JSON` 示例：

```json
{
  "web-admin": {
    "project": "demo",
    "web": "main",
    "auth_type": "password",
    "tenant": false,
    "flow_class": "my_framework.flows.login:LoginFlow",
    "smoke": "tests/test_login.py"
  }
}
```

## 执行安全

生产环境使用 `MOCK_MODE=false`、`ALLOW_MOCK=false`。worker、Redis、数据库和 Sonic 不应直接暴露到公网；所有 Token 通过环境变量或密钥管理服务注入。
