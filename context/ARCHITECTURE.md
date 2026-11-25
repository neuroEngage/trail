# ARCHITECTURE

```
React + TS UI  ->  FastAPI (REST, OpenAPI)  ->  PostgreSQL (metadata)
                          |                          
                          +-> Job queue (arq/RQ) -> Workers (profile, assess, clean, report)
                          |
                          +-> Compute: DuckDB / Polars over Parquet
                          |
                          +-> Object store: /data/datasets/{dataset_id}/v{n}.parquet
```

## Repo layout (monorepo)
```
/backend
  /app
    /platform      # W0: auth, roles, audit, versioning, jobs
    /connect       # W1 (F1)
    /catalogue     # W2 (F2)
    /profiler      # W3 (F3)
    /quality       # W4 (F4)
    /investigate   # W5 (F5)
    /cleaner       # W6 (F6)
    /compare       # W7 (F7)
    /reports       # W8 (F8)
  /tests
/frontend
  /src/features/{connect,catalogue,profiler,quality,investigate,cleaner,compare,reports}
  /src/shared
/context           # this pack
/testdata
/docker-compose.yml
```

## Key flows
- **Snapshot:** F1 reads source (read-only) -> writes `v1.parquet` -> registers `dataset` + `dataset_version(kind=snapshot)`.
- **Version:** F6 applies approved actions to a copy of a version -> writes `v(n+1).parquet` -> `dataset_version(kind=cleaned, parent=n)`. Parents are never overwritten.
- **Jobs:** long work (profile, assess, apply, report) is a queued job with `status`, `progress 0-100`, cancel support, and survives restart.
- **Cache:** profile results keyed by `dataset_version_id`; new version = new profile.
- **Audit:** every create/update/delete/apply/undo/approve writes `audit_event(actor, action, entity, before, after, ts)`.

## Integration rule
Feature modules talk to each other only through the API contract or the shared `platform` services (versioning, audit, jobs). No cross-imports between feature folders.
