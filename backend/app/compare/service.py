import json
import re
from typing import Dict, Any, List, Optional
import duckdb
import pandas as pd
from sqlalchemy.orm import Session
from fastapi import HTTPException

from backend.app.platform.models import Comparison, DatasetVersion
from backend.app.platform.versioning import read_version_df
from backend.app.platform import jobs

def run_comparison(
    db: Session,
    version_a_id: str,
    version_b_id: str,
    sql_query: Optional[str] = None,
    job_id: Optional[str] = None
) -> Comparison:
    v_a = db.query(DatasetVersion).filter(DatasetVersion.id == version_a_id).first()
    v_b = db.query(DatasetVersion).filter(DatasetVersion.id == version_b_id).first()
    if not v_a or not v_b:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "One or both versions not found", "details": {}}})

    if job_id:
        jobs.update_job_progress(job_id, 20, "running")

    df_a = read_version_df(v_a)
    df_b = read_version_df(v_b)

    # 1. Summary deltas
    row_delta = len(df_b) - len(df_a)
    col_delta = len(df_b.columns) - len(df_a.columns)

    # 2. Per column comparison
    common_cols = [c for c in df_a.columns if c in df_b.columns]
    cols_comparison = {}

    for c in common_cols:
        s_a = df_a[c]
        s_b = df_b[c]
        null_a = int(s_a.isna().sum())
        null_b = int(s_b.isna().sum())
        distinct_a = int(s_a.nunique())
        distinct_b = int(s_b.nunique())

        col_dict = {
            "name": c,
            "null_count_a": null_a,
            "null_count_b": null_b,
            "null_delta": null_b - null_a,
            "distinct_a": distinct_a,
            "distinct_b": distinct_b,
            "distinct_delta": distinct_b - distinct_a
        }

        if pd.api.types.is_numeric_dtype(s_a) and pd.api.types.is_numeric_dtype(s_b):
            m_a = float(s_a.mean()) if not s_a.empty else 0.0
            m_b = float(s_b.mean()) if not s_b.empty else 0.0
            col_dict.update({
                "mean_a": round(m_a, 4),
                "mean_b": round(m_b, 4),
                "mean_delta": round(m_b - m_a, 4),
                "min_a": float(s_a.min()) if not s_a.empty else None,
                "min_b": float(s_b.min()) if not s_b.empty else None,
                "max_a": float(s_a.max()) if not s_b.empty else None,
                "max_b": float(s_b.max()) if not s_b.empty else None
            })

        cols_comparison[c] = col_dict

    if job_id:
        jobs.update_job_progress(job_id, 60)

    # 3. Optional SQL query execution with DuckDB sandbox (D11)
    sql_result = None
    if sql_query:
        # Validate security per D11: single statement, SELECT only
        cleaned_sql = sql_query.strip().rstrip(";")
        if ";" in cleaned_sql:
            raise HTTPException(status_code=400, detail={"error": {"code": "SQL_VIOLATION", "message": "Only single SQL statement permitted", "details": {}}})
        
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "COPY", "PRAGMA"]
        for word in forbidden:
            if re.search(rf"\b{word}\b", cleaned_sql, re.IGNORECASE):
                raise HTTPException(status_code=400, detail={"error": {"code": "SQL_VIOLATION", "message": f"Disallowed SQL keyword '{word}'", "details": {}}})

        try:
            con = duckdb.connect(database=":memory:", read_only=False)
            con.register("dataset_a", df_a)
            con.register("dataset_b", df_b)
            # Run query with limit
            limited_sql = f"SELECT * FROM ({cleaned_sql}) LIMIT 1000"
            res_df = con.execute(limited_sql).fetchdf()
            sql_result = {
                "columns": list(res_df.columns),
                "rows": res_df.where(pd.notnull(res_df), None).to_dict(orient="records")
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail={"error": {"code": "SQL_EXECUTION_ERROR", "message": str(e), "details": {}}})

    comp_result = {
        "version_a_id": version_a_id,
        "version_b_id": version_b_id,
        "version_a_number": v_a.number,
        "version_b_number": v_b.number,
        "row_count_a": len(df_a),
        "row_count_b": len(df_b),
        "row_delta": row_delta,
        "col_delta": col_delta,
        "columns": cols_comparison,
        "sql_result": sql_result
    }

    comparison = Comparison(
        version_a=version_a_id,
        version_b=version_b_id,
        result_json=json.dumps(comp_result, default=str)
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    if job_id:
        jobs.mark_job_done(job_id)

    return comparison
