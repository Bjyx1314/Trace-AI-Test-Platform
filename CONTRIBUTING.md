# 贡献指南

感谢参与 TraceAI Test Platform。提交代码即表示你有权贡献该内容，并同意按 Apache-2.0 许可证发布。

## 开发流程

1. 从 `main` 创建功能分支。
2. 保持改动聚焦，并为行为变化补充测试或文档。
3. 运行后端测试与前端构建。
4. 执行敏感信息检查：`powershell -File scripts/check-secrets.ps1`。

提交身份请使用代码托管平台提供的 noreply 邮箱，避免把个人或组织邮箱写入公开 Git 历史。
5. 提交 Pull Request，说明动机、实现、验证结果和兼容性影响。

## 代码约定

- Python 遵循现有类型标注和异步风格。
- React/TypeScript 遵循现有组件与主题体系。
- 不提交真实账号、Token、Cookie、内网地址、客户名称、业务数据或安装包。
- 示例域名使用 `example.com`、`example.test`，示例公网 IP 使用 RFC 5737 地址。
- 新增外部集成必须默认关闭，并提供无敏感值的配置模板。

## 问题反馈

普通缺陷可通过 Issue 提交。安全问题请遵循 [SECURITY.md](SECURITY.md)，不要公开披露利用细节。
