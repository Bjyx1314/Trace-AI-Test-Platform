# Reference — Risk Heuristics & Test Type Selection

## Change-Type → Test Type Matrix

| Change label | Functional | API/Contract | Integration | UI/E2E | Performance | Security |
|--------------|:----------:|:------------:|:-----------:|:------:|:-----------:|:--------:|
| feature | ● | ● | ● | ● | ○ | ○ |
| bugfix | ● | ○ | ○ | ○ | — | — |
| refactor | ● | ○ | ● | ○ | ○ | — |
| api-contract | ○ | ● | ● | ○ | — | ○ |
| data-model | ● | ○ | ● | — | ○ | — |
| config | ● | — | ● | ○ | — | — |
| dependency-bump | ○ | ○ | ● | — | — | — |
| messaging | ● | ○ | ● | — | ○ | — |
| scheduled-job | ● | ○ | ● | — | — | — |
| performance | ○ | — | ● | — | ● | — |
| security | ○ | ○ | ● | ○ | — | ● |

● = usually required, ○ = consider, — = rarely needed unless combined with other labels.

## Blast Radius Signals

Treat as **P0/P1** when changed code matches any:

- Authentication / authorization / session / tenant isolation
- Payment, billing, refund, pricing, settlement
- Core domain lifecycle (order, inventory, fulfillment, contract — domain-agnostic)
- Shared validation, serialization, global exception handler
- Database migration or schema alter
- Message queue consumer with idempotency or ordering logic
- Public API / Feign interface used by other services
- Shared `*-common` module enum or constant used widely

## Caller Impact Heuristics

| Signal | Upstream impact |
|--------|-----------------|
| Public method / API signature changed | High — all direct callers |
| DTO field added (optional) | Low–Medium — backward compatible |
| DTO field removed or renamed | High — all serializers/consumers |
| Feign interface method changed | High — all client services |
| Internal service impl only | Medium — entry smoke sufficient |
| Private method, single caller | Low |
| Enum value added | Low–Medium — switch/if chains |
| Enum value removed/renamed | High |
| `@RocketMQMessageListener` topic/group changed | High — routing + consumer group rebalance |

## Extension-Point Impact

| Signal | Required action |
|--------|-----------------|
| New call in one `*Handler` | Grep all sibling handlers |
| New `*Fill` or pipeline step | Trace full fill order for V1/V2/V3 |
| Logic in `Abstract*Base` class | All subclasses inherit — list subclasses |
| Conditional feature flag in Manager | Test flag ON/OFF for **each** entry that calls Manager |

## Regression Scope Shortcuts

| Scenario | Suggested regression boundary |
|----------|------------------------------|
| Single REST endpoint | Endpoint + auth + validation + DB side effect |
| Feign `*Api` change | All known consumers (in-repo clients + 待确认 external) |
| Shared util / `*-common` | All importing modules (grep evidence) |
| DB migration | Migration applied + CRUD + reporting queries |
| MQ consumer new/changed | Happy path + duplicate message + malformed payload |
| Middleware / interceptor / filter | Sample one route per business area |
| Dependency version bump | Features importing that artifact + startup smoke |
| Maven `*-biz` Manager change | All services/handlers injecting that Manager |

## Test Coverage Audit

When analyzing changed modules:

```bash
# Count test files in changed modules
find <changed-module>/src/test -name "*Test*.java" 2>/dev/null
```

| Finding | Report as | Test recommendation |
|---------|-----------|---------------------|
| Zero tests in changed module | **自动化覆盖空白** | Raise risk + suggest manual depth |
| Unit tests exist adjacent | Lower execution risk | Run existing suite + delta cases |
| Only web-layer tests | Partial coverage | Add service-layer scenarios for new branches |

## Test Data Suggestions

Include in output when relevant:

- **Boundary values**: min/max, empty, null, zero, default sentinel (e.g. `0`, `"0"`)
- **Role / tenant matrix**: admin / user / cross-tenant negative
- **State prerequisites**: draft vs active, pending vs completed
- **Concurrency**: duplicate submit, parallel MQ, race on same unique key
- **Idempotency**: retry same request-id, message redelivery, compensation replay
- **Virtual vs real entity variants** (if domain has stub accounts / test tenants)

## Environment Matrix

| Change involves | Suggest env coverage |
|-----------------|---------------------|
| Feature flag / Apollo / Nacos | ON + OFF; document default |
| Multi-tenant | ≥2 tenants; cross-tenant access denied |
| External RPC (Feign) | success + timeout + business error + null body |
| MQ | topic available; consumer group not conflicting |
| SNAPSHOT dependency | Staging Nexus mirror matches CI |
| DDL change | Env where migration already applied |

## Risk-to-Test Depth

| Risk | Minimum test depth |
|------|-------------------|
| P0 | Feature regression + integration + negative + idempotency + rollback/post-deploy check |
| P1 | Core paths + key edge cases + entry smoke per handler/listener |
| P2 | Changed-path verification + spot regression |
| P3 | Targeted check or dev-verified + smoke |

## Deep Mode Triggers (Auto)

Upgrade output when any:

- >30 files or >500 lines in business modules
- MQ + DB + external RPC in same feature bundle
- Overall P0
- User says 深入分析

Add: mermaid diagram, entry coverage matrix, per-entry test rows.

## Output Quality Checklist

Before delivering the report, verify:

- [ ] Commits clustered into feature bundles (not raw file dump)
- [ ] Maven scan checklist applied (if Java multi-module)
- [ ] Entry coverage matrix filled or N/A stated
- [ ] TODO/待实现 paths downgraded or in 待确认项
- [ ] Every P0 test item links to a logic change or caller
- [ ] "可不测" section exists and is justified
- [ ] At least one negative case per new branch
- [ ] Uncertain items in 待确认项, not omitted
- [ ] Dependency bumps listed with integration smoke suggestion
- [ ] Regression scope proportional to risk
