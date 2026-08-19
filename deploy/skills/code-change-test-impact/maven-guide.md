# Maven Multi-Module Projects — Test Impact Guide

Generic guidance for Java/Maven repos. Module naming varies by project; infer roles from `pom.xml` `<modules>` and package layout — do not assume fixed names like `trade-order-center-*`.

## Detect Maven Multi-Module

```bash
# Root pom has <packaging>pom</packaging> and <modules>
grep -l "<modules>" pom.xml
```

Common layer suffixes (adapt to actual module names):

| Typical suffix / role | Common contents | Test focus when changed |
|----------------------|-----------------|-------------------------|
| `*-api` / `*-intf` | DTO, Feign interfaces, enums, constants | Contract compatibility, serialization, Feign consumers |
| `*-web` / `*-app` | Controllers, Boot main, filters | HTTP API, auth, request validation |
| `*-biz` / `*-service` | Service impl, Manager, Handler, Listener | Business flows, branching, transactions |
| `*-infra` / `*-repository` | PO, Mapper, DAO, Repository impl | SQL, indexes, data mapping |
| `*-common` | Shared enums, utils, constants | Wide blast radius — grep all usages |
| `*-client` | Feign wrappers for outbound calls | Downstream integration |
| `*-sched` / `*-job` | XXL-Job, `@Scheduled` | Job trigger, failure, idempotency |
| `*-model` | Domain models (if split) | Field changes propagate to mappers |

## Multi-Module Scan Checklist

Run during Step 1.5 when Maven multi-module is detected:

| # | Scan | How | Test implication |
|---|------|-----|------------------|
| 1 | Module diff map | `git diff --name-only <BASE>..HEAD` grouped by top-level module | Which layers touched |
| 2 | Dependency bumps | `git diff **/pom.xml` | Cross-team RPC/API regression; SNAPSHOT availability |
| 3 | Schema / DDL | `**/ddl.sql`, `**/db/migration/**`, `**/liquibase/**`, `**/flyway/**` | Migration executed in env; column defaults |
| 4 | Persistence | `*PO.java`, `*Mapper.xml`, `*Repository*.java` | CRUD, null handling, unique constraints |
| 5 | Inbound API | `@RestController`, `@RequestMapping`, `*Controller.java` | New/changed endpoints |
| 6 | Outbound RPC | `@FeignClient`, `*Api.java`, `*Client.java` | External service failure paths |
| 7 | Messaging | `@RocketMQMessageListener`, `@RabbitListener`, `@KafkaListener` | Topic/group, idempotency, retry, DLQ |
| 8 | Feature flags | `@Value`, `@ConfigurationProperties`, `@ApolloJsonValue`, Nacos config | ON/OFF matrix; default value |
| 9 | Scheduled / compensate | `@XxlJob`, `@Scheduled`, `*Compensate*`, `*Job.java` | Manual run + failure recovery |
| 10 | Fallback / degrade | `*FallBack.java`, `*FallbackFactory` | Downstream unavailable behavior |
| 11 | Extension points | `implements *Handler`, `extends Abstract*`, `*Fill`, `*Strategy` | Full implementation enumeration |
| 12 | Test coverage | `**/*Test*.java`, `**/*IT.java` in changed modules | Mark automation gaps |

## Grep Patterns (Generic)

```bash
# Feign / RPC API
rg "@FeignClient|FeignClient" --glob "*.java"
rg "interface \w+Api" --glob "*-api/**/*.java"

# MQ consumers
rg "RocketMQMessageListener|KafkaListener|RabbitListener" --glob "*.java"

# Feature configuration
rg "@Value\(|ConfigurationProperties|ApolloJsonValue" --glob "*.java"

# Jobs
rg "@XxlJob|@Scheduled" --glob "*.java"

# Handlers / strategies
rg "implements \w+Handler|extends Abstract\w+Handler" --glob "*.java"

# Stubs
rg "TODO|FIXME|待实现" --glob "*.java" <changed-paths>

# Tests in changed modules
glob **/*Test*.java under changed module directories
```

## Dependency Bump Analysis

When `pom.xml` `<version>` changes:

| Signal | Action |
|--------|--------|
| Internal `*-api` version bump | Search repo + consuming services for compatibility |
| `*-SNAPSHOT` | Flag: confirm artifact published to corporate Nexus |
| Spring Boot / Cloud BOM | Smoke core startup + one RPC + one DB path |
| Third-party major bump | Read library release notes; focus on deprecated APIs used in diff |

Output table:

| Artifact | Old → New | Suggested test |
|----------|-----------|----------------|
| ... | ... | Integration smoke on features using this dependency |

## Layer → Test Type Mapping

| Changed layer | Primary tests | Secondary tests |
|---------------|---------------|-----------------|
| `*-api` DTO/Feign | Contract, field null/default, JSON serialize | Consumer compile (if multi-repo) |
| `*-web` Controller | API functional, validation, auth | Swagger/OpenAPI diff |
| `*-biz` Service/Handler | End-to-end business flow | Unit for pure logic in Manager |
| `*-infra` Mapper/PO | DB field persist/query, migration | SQL explain for new queries |
| MQ Listener | Message publish → consume → DB state | Duplicate delivery, poison message |
| `*-sched` Job | Trigger job, verify side effect | Job failure + retry |

## Entry Coverage Matrix Template

Use when new logic is injected into shared pipelines (submit handlers, fill chains, filters):

| Entry (class or route) | Module | 接入新逻辑? | 证据 |
|------------------------|--------|-------------|------|
| FooSubmitHandler | *-biz | ✅ | diff contains fillXxx() call |
| BarSubmitHandler | *-biz | ❓ | no diff; grep shows no call — 待确认 |
| V3SubmitFillChain | *-biz | ✅ | PartyFill updated |

## Common Maven Flow Patterns (Test Angles)

| Pattern | What to trace | Typical risk |
|---------|---------------|--------------|
| Submit handler chain | All `*Handler` for same `submitType` | Missed entry → wrong data on some channels |
| Fill / enrich pipeline | All `*Fill`, `*Enricher` in order | Partial field population |
| Callback controller | External system → status update | Idempotency, signature, wrong state |
| Compensation service | Admin/API replay of failed ops | Double-apply, partial update |
| Repository save idempotent | `DuplicateKeyException`, select-before-insert | Concurrent duplicate records |
| Dual exposure | Same capability in Controller + Feign `*Api` | One path updated, other missed |

## Low-Code / aPaaS (If Present)

If diff touches `ModelDataService`, `*ENTITY_ID`, or generated model classes:

- Label as `data-model` + `feature`
- Test via business API that reads/writes the entity, not generated code directly
- Verify upsert vs insert semantics

## Project Docs to Prefer

Search order (first found wins; none required):

1. `AGENTS.md`, `README.md`
2. `**/architecture.md`, `**/conventions.md`
3. `**/ddl.sql` or migration folder
4. Root `pom.xml` `<modules>`

## Anti-Patterns (Maven-Specific)

- Assuming all modules follow `api/biz/infra/web` — read actual `<modules>`.
- Testing only the Controller when Feign `*Api` also exposes the same operation.
- Ignoring `*-common` enum changes — often affects every module.
- Missing MQ consumer when feature is "async half" of a sync API.
- Recommending DB tests without checking whether DDL/migration is in repo vs DBA-only.
