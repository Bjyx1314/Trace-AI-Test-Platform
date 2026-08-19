---
name: code-graph-scan
description: >-
  Scans a business code repository and extracts a code-fact graph: pages,
  components, API endpoints, services, DB tables, MQ topics, and the edges
  between them (page→api calls, api→service handled_by, service→db accesses,
  component→component defines/belongs_to). Outputs structured JSON for a test
  platform's impact-analysis graph. Supports frontend (routes/pages/components/
  api calls) and backend (Controller/Service/DAO/DB/MQ) — generic + Java/Maven.
---

# Code Fact Graph Scan

Extract the **code-fact relationship model** of a repository so a test platform
can answer "changing this file affects which pages / apis / services / cases".

This is fact extraction, NOT business modeling. Only emit what you can evidence
from the code (grep hit / file:line / route registry). Prefer deterministic
extraction (route tables, `@RequestMapping`, axios/fetch calls); use inference
only as fallback and mark those edges `llm_inferred`.

## When to Apply

- Building or refreshing the code-fact graph for a business repo.
- Incremental update after an MR (scan only changed files + their out-edges).

## Inputs — Resolve Before Scan

| Input | How to obtain |
|-------|---------------|
| Repo root | current working directory (already checked out) |
| Scope | full scan, or a changed-file list (incremental) |
| repo label | provided in the prompt (e.g. `order-web`) |

## Node Types (7)

`file` `component` `page` `api` `service` `db` `mq`

## Stable Node IDs (must follow exactly)

| Type | ID format | Example |
|------|-----------|---------|
| page | `page:{repo}:{route}` | `page:order-web:/order/detail` |
| api | `api:{METHOD}:{path}` | `api:POST:/api/order/submit` |
| service | `svc:{repo}:{Class}` | `svc:order-service:AmountCalculateService` |
| component | `comp:{repo}:{name}` | `comp:order-web:CouponDialog` |
| file | `file:{repo}:{path}` | `file:order-web:src/views/Order.vue` |
| db | `db:{schema.table}` | `db:order.t_order` |
| mq | `mq:{topic}` | `mq:order-paid` |

Dynamic path segments → placeholder: numeric → `{id}`, uuid → `{uuid}`.

## Edge Types

`defines` (file→component/service) · `belongs_to` (component→page) ·
`calls` (page/component→api, service→service) · `handled_by` (api→service) ·
`accesses` (service→db) · `produces`/`consumes` (service→mq).

Each edge: `{from, to, edge_type, source, confidence, evidence}`.
`source` ∈ `static_scan` | `llm_inferred`. Always include `evidence` (file:line / grep).

## Scan Workflow

```
- [ ] Step 0: Read AGENTS.md / README for module layout (if present)
- [ ] Step 1: Frontend — find route table → pages; components; api calls (axios/fetch/request)
- [ ] Step 2: Backend — Controllers → api nodes + handled_by service; Service→Service calls; DAO→db; MQ producers/consumers
- [ ] Step 3: Cross-layer — page→api by matching called path to api node; Feign/openapi consumers
- [ ] Step 4: For incremental — limit to changed files + re-extract their out-edges; emit renames [{old,new}] from git -M
- [ ] Step 5: Emit JSON
```

## Output Contract (platform mode)

Output ONLY one JSON object, no markdown fence:

```json
{
  "schema_version": "1.0",
  "repo": "order-web",
  "scan_mode": "full | incremental",
  "nodes": [
    {"node_id": "page:order-web:/order/detail", "node_type": "page", "name": "订单详情页", "attrs": {"route": "/order/detail", "file": "src/views/OrderDetail.vue"}}
  ],
  "edges": [
    {"from": "page:order-web:/order/detail", "to": "api:POST:/api/coupon/apply", "edge_type": "calls", "source": "static_scan", "confidence": 0.95, "evidence": "OrderDetail.vue:132 axios.post('/api/coupon/apply')"}
  ],
  "renames": [{"old": "svc:order-service:OldName", "new": "svc:order-service:NewName"}]
}
```

Rules: no node without evidence in attrs or its defining file; no edge without `evidence`.
Never claim caller coverage complete without a grep/search backing it.
