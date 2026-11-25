# DATA_MODEL (PostgreSQL metadata)

| Table | Key columns |
|---|---|
| user | id, email, role (analyst/approver/admin), created_at |
| source | id, type (file/postgres), config_encrypted, status, owner_id, created_at |
| dataset | id, source_id, name, owner_id, created_at |
| dataset_version | id, dataset_id, number, kind (snapshot/cleaned), parent_version_id, parquet_path, row_count, col_count, checksum, created_by, created_at. **Immutable.** |
| job | id, type, entity_id, status (queued/running/done/failed/cancelled), progress, error, created_at, finished_at |
| profile | id, dataset_version_id (unique), results_json, created_at |
| rule | id, dataset_id, name, dimension, expression, severity, enabled, created_by |
| assessment | id, dataset_version_id, scores_json (per dimension), overall, created_at |
| issue | id, assessment_id, type, dimension, column, severity, affected_count, why_text, status (open/valid/ignored), created_at |
| issue_record | issue_id, row_index, column, value |
| action | id, issue_id, type, config_json, status (proposed/approved/applied/undone), rows_affected, proposed_by, approved_by, result_version_id |
| comparison | id, version_a, version_b, metrics_json, result_json, created_at |
| report | id, dataset_id, version_ids, sections, format, file_path, share_token, expires_at, created_at |
| audit_event | id, actor_id, action, entity_type, entity_id, before_json, after_json, created_at |

## Definitions
- **Snapshot:** frozen copy of source data taken in F1.
- **Version:** result of an applied cleaning action in F6. Chain: snapshot v1 -> v2 -> v3.
- **Dimensions:** completeness, validity, uniqueness, consistency, accuracy, timeliness.
