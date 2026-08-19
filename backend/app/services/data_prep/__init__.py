"""测试数据准备与状态编排（方案 V3）。

MVP-0 只落地【统一变量注入引擎】+ 人工数据要求（manual_values）：
- injection：把 `${alias.field}` 从 ExecutionContext 变量确定性替换进 web/app 步骤文本、api 变量；
- context：从用例的 TestDataRequirement 生成 ExecutionContext（变量 + 凭证）。

后续阶段（AUTO 造数、场景编排、能力认证、Validator、清理）在此包内逐步扩展。
"""
