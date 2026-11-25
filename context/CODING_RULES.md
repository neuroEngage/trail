# CODING_RULES
- Python 3.12, type hints everywhere, `ruff` + `mypy --strict` on backend; `eslint` + `tsc --noEmit` on frontend.
- API errors: `{ "error": { "code": "SNAKE_CASE", "message": "human readable", "details": {} } }`. Never leak stack traces.
- Logging: structured JSON. Never log credentials, tokens, or cell values.
- Never modify source data or any existing `dataset_version` file.
- All writes to state go through `platform.audit.record(...)`.
- Every endpoint checks role. Default deny.
- UI: every screen has loading, empty and error states.
- Naming: snake_case Python, camelCase TS, PascalCase components, endpoints `/api/v1/...`.
- Small PRs: one feature per branch `feat/Fx-name`; tag `v0.1-Fx` on merge.
