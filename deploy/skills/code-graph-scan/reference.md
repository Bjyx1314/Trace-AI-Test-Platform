# Code Graph Scan Reference

## Frontend extraction patterns

| Target | Where to look |
|--------|---------------|
| Routes / pages | `router/index.*`, `routes.*`, `createRouter`, `<Route path=`, Next.js `pages/` or `app/` dirs |
| Components | `.vue` / `.jsx` / `.tsx` component files; imports between them = defines/belongs_to |
| API calls | `axios.get/post`, `fetch(`, `request(`, `useQuery`, service wrapper modules — extract METHOD + path |
| Shared components | grep imports of a component across pages |

## Backend extraction patterns (generic + Java/Maven)

| Target | Where to look |
|--------|---------------|
| API endpoints | `@RestController`/`@RequestMapping`/`@GetMapping/@PostMapping`; Express `app.get/post`, FastAPI `@router.get` |
| Service methods | `@Service` classes, service-layer packages |
| DAO / DB | `@Mapper`, `@Repository`, MyBatis XML, JPA entities → `db:{schema.table}` |
| MQ | `@KafkaListener`, `@RocketMQMessageListener`, producer `send(topic` |
| Feign / cross-service | `@FeignClient` → consumer calls provider's api node (cross-repo edge by matching path) |

## Cross-layer wiring

- **page→api**: match the path a page's api call uses to an `api:{METHOD}:{path}` node.
- **api→service**: controller method body calls a service → `handled_by`.
- **service→db**: service calls a DAO that maps to a table → `accesses`.
- **cross-repo**: consumer `calls api:POST:/x` ↔ provider `api:POST:/x handled_by svc` — matched by identical api node id.

## Confidence guidance

| Evidence | source | confidence |
|----------|--------|-----------|
| Direct code call with file:line | static_scan | 0.9–1.0 |
| Route/annotation registry | static_scan | 0.95 |
| Inferred by naming/heuristic only | llm_inferred | 0.4–0.6 |

## Incremental scan

- Only re-extract out-edges of changed files.
- Emit `renames` from `git diff -M --name-status` (R100 lines) as `{old, new}` node id pairs.
- Do NOT delete untouched nodes — the platform handles staleness by version.

## Stop rules

- Do not traverse more than needed to establish direct edges from changed files.
- If a caller set cannot be fully enumerated (dynamic dispatch), emit what you find + note `attrs.partial=true` on the node; do not fabricate edges.
