# DECISIONS (append-only; PM approves changes)

| # | Date | Decision | Reason |
|---|---|---|---|
| D1 | 2026-11-25 | Backend: Python + FastAPI | Data ecosystem, fast API work |
| D2 | 2026-11-25 | Frontend: React / Modern Web Dashboard + Tailwind/Lucide icons | Clean, typed, responsive |
| D3 | 2026-11-25 | Metadata: SQLite (local default) / PostgreSQL (configurable via DATABASE_URL) | Fast local iteration without Docker; production Postgres support |
| D4 | 2026-11-25 | Data store: one Parquet file per dataset version | Immutability, cheap undo |
| D5 | 2026-11-25 | Compute: DuckDB (SQL checks, compare) + Polars/Pandas (transforms) | Fast on 1M+ rows |
| D6 | 2026-11-25 | Jobs: In-process async background queue with persistent DB tracking | Simple local execution without external Redis daemon |
| D7 | 2026-11-25 | Auth: email/password + roles Analyst/Approver/Admin | F6 needs Approver |
| D8 | 2026-11-25 | Credentials: Fernet encryption, key from env; DB users must be read-only | Security |
| D9 | 2026-11-25 | **Accuracy** (v1) = conformance to user-defined reference rules; optional, hidden if no rules | Doc is ambiguous |
| D10 | 2026-11-25 | **Timeliness** (v1) = freshness of a user-chosen date column vs an expected max age; optional | Needs a date column |
| D11 | 2026-11-25 | **Saved SQL (F7)**: single statement, SELECT only, read-only DuckDB connection, 30s timeout, 100k row limit | Security |
| D12 | 2026-11-25 | Score: each dimension 0-100 = 100 x (1 - failing_cells_or_rows / total); overall = weighted mean; every deduction links to an issue | Explainable |
| D13 | 2026-11-25 | Limits v1: upload max 500 MB; profile target 1M rows in < 3 min | Gate performance |
| D14 | 2026-11-25 | Local execution: standalone without Docker; remote PostgreSQL server supported as data feed | User constraint for development |
