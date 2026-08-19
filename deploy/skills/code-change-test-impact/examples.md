# Examples — Code Change Test Impact Analysis

## Example 1: Bugfix — Order Status Update

**Input**: User asks to evaluate commit fixing "order stuck in PENDING after payment callback".

**Change summary**: Modified `PaymentCallbackHandler.handle()` — added retry when lock contention; changed status transition guard.

**Output excerpt**:

### 2.1 影响的功能点

| 功能/模块 | 影响类型 | 影响说明 | 置信度 |
|-----------|----------|----------|--------|
| 支付回调处理 | 直接 | 回调成功后的订单状态流转 | 高 |
| 订单详情页 | 间接 | 状态展示依赖后端状态 | 中 |

### 2.3 调用关系

| 方向 | 调用方/被调用方 | 影响级别 | 说明 |
|------|-----------------|----------|------|
| 上游 | `POST /api/payment/callback` | 高 | 入口未变，处理逻辑变 |
| 下游 | `OrderService.updateStatus()` | 高 | 新增重试分支 |

### 4.1 必测范围

| 优先级 | 测试项 | 类型 | 说明 |
|--------|--------|------|------|
| P0 | 支付成功回调后订单变为 PAID | 接口+集成 | 原缺陷复现路径 |
| P0 | 并发双重回调幂等 | 接口 | 仅一次状态变更 |
| P1 | 回调时订单已取消 | 接口 | 新 guard 分支 |

---

## Example 2: Refactor — Shared Auth Middleware

**Input**: PR refactors JWT validation into `AuthMiddleware`, no intended behavior change.

**Risk**: P1 — shared middleware, wide blast radius.

**Output excerpt**:

### 2.3 调用关系

| 方向 | 调用方/被调用方 | 影响级别 | 说明 |
|------|-----------------|----------|------|
| 上游 | 全部 `/api/*` 路由 (~40) | 中 | 接口不变，内部实现迁移 |

### 4.1 必测范围

| 优先级 | 测试项 | 类型 | 说明 |
|--------|--------|------|------|
| P0 | 有效 token 访问受保护接口 | 接口 | 核心冒烟 |
| P0 | 过期/伪造 token 返回 401 | 接口 | 安全回归 |
| P1 | 各业务线各抽 1 个代表接口 | 接口 | 抽样回归 |

### 4.4 可不测 / 低优先级

| 项 | 理由 |
|----|------|
| 每个接口全量遍历 | 中间件逻辑统一，抽样即可；如有自动化 API 套件优先跑套件 |

---

## Example 3: API Contract Change

**Input**: `GET /users/{id}` response adds field `lastLoginAt`, removes `legacyId`.

**Output excerpt**:

### 风险评估

| 区域 | 风险等级 | 理由 |
|------|----------|------|
| 移动端旧版本 | P0 | 可能依赖 `legacyId` 字段 |
| Web 管理后台 | P1 | 同一 API 消费方 |

### 5. 推荐测试用例

| ID | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|----|------|----------|------|----------|--------|
| TC-01 | 新客户端解析响应 | 新版本 App | 请求用户信息 | 含 `lastLoginAt`，无 `legacyId` | P0 |
| TC-02 | 旧客户端兼容性 | 旧版本 App | 请求用户信息 | 不崩溃；确认降级策略 | P0 |

---

## Example 4: Maven Multi-Module — New Entity + MQ + Submit Handlers

**Input**: "分析最近一周改动，给出测试建议". Repo: `acme-platform` with modules `user-api`, `user-biz`, `user-infra`, `user-web`.

**Step 0**: Read root `pom.xml` → 4 modules. No AGENTS.md.

**Step 1**: 52 files, +1800 lines. Commit clusters:

- 「参与方/account-party」18 commits
- 「子产品线 auto-fill」9 commits
- 「幂等 MQ」4 commits
- 「升级依赖」3 commits

**Step 1.5 Maven scan finds**:

- New table `account_party` in `docs/ddl.sql`
- `PartyCreatedConsumer` on topic `CERT_ENTITY_CREATED`
- `@Value("${feature.party.enabled:false}")` in `PartyProperties`
- `PartyFillManager` injected into 2 of 5 `*SubmitHandler` classes

**Output excerpt**:

### 1. 变更概览

- **功能聚类**: 参与方能力 / 子产品线填充 / MQ 幂等 / 依赖升级
- **变更摘要**: 新增参与方模型与 MQ 消费，下单链路自动填充 partyId；部分 Handler 已接入，需确认全覆盖。

### 2.4 入口覆盖矩阵

| 入口 | 是否接入 PartyFill | 证据 |
|------|-------------------|------|
| SelfSubmitHandler | ✅ | diff |
| BrokerSubmitHandler | ✅ | diff |
| SaasSubmitHandler | ❓ | grep 无 fillParty 调用 — 待确认 |
| OpenApiSubmitHandler | ❓ | 未追踪 |
| V3SubmitFillChain | ✅ | PartyFill.java diff |

### 3. 风险评估

| 区域 | 风险等级 | 理由 |
|------|----------|------|
| 参与方 + 下单 | P0 | MQ + DB + 外部 RPC；Handler 未全覆盖 |
| fillSupplierId | P3 | 方法体 TODO，未实现 |

### 4.4 可不测 / 低优先级

| 项 | 理由 |
|----|------|
| 供给方 partyId 填充 | 代码 TODO，当前不生效 |

### 7. 待确认项

- [ ] SaasSubmitHandler / OpenApiSubmitHandler 是否应接入 PartyFill
- [ ] DDL 是否已在测试环境执行
- [ ] `item-api:2.1.0-SNAPSHOT` 是否已发布到 Nexus

---

## Trigger Phrases (User → Skill)

Apply this skill when user says things like:

- "分析这次代码改动的影响范围"
- "帮我出测试范围和回归建议"
- "评估开发修改对功能点和调用方的影响"
- "这个 PR 需要测什么"
- "review 一下分支改动，给测试用例"
- "最近 N 天/周的改动测试建议"
- "Maven 项目变更影响分析"
