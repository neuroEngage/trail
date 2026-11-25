import json
import math
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.app.platform.models import DatasetVersion, Profile
from backend.app.platform.versioning import read_version_df
from backend.app.platform import jobs

# Semantic patterns
EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")
PHONE_REGEX = re.compile(r"^\+?[0-9\-\s\(\)]{7,20}$")
ID_REGEX = re.compile(r"^[A-Za-z0-9_\-]{4,20}$")
ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?")

def detect_semantic_type(series: pd.Series) -> Dict[str, Any]:
    non_nulls = series.dropna().astype(str).str.strip()
    total = len(non_nulls)
    if total == 0:
        return {"type": "unknown", "confidence": 0.0}

    # Sample up to 500 rows for fast detection
    sample = non_nulls.head(500)
    n = len(sample)

    # Email
    email_matches = sum(bool(EMAIL_REGEX.match(val)) for val in sample)
    if email_matches / n >= 0.8:
        return {"type": "email", "confidence": round(email_matches / n, 2)}

    # Phone
    phone_matches = sum(bool(PHONE_REGEX.match(val)) and any(c.isdigit() for c in val) for val in sample)
    if phone_matches / n >= 0.8:
        return {"type": "phone", "confidence": round(phone_matches / n, 2)}

    # Date
    date_matches = sum(bool(ISO_DATE_REGEX.match(val)) for val in sample)
    if date_matches / n >= 0.8:
        return {"type": "date", "confidence": round(date_matches / n, 2)}

    # ID
    col_name = str(series.name).lower() if series.name else ""
    id_matches = sum(bool(ID_REGEX.match(val)) for val in sample)
    if (id_matches / n >= 0.9 and series.nunique() / len(series) > 0.8) or "id" in col_name:
        return {"type": "id", "confidence": 0.95}

    # Currency / Amount
    if "amount" in col_name or "price" in col_name or "salary" in col_name or "cost" in col_name:
        return {"type": "currency", "confidence": 0.95}

    if pd.api.types.is_numeric_dtype(series):
        return {"type": "numeric", "confidence": 1.0}

    return {"type": "categorical" if series.nunique() < 50 else "text", "confidence": 0.8}

def profile_dataset_version(db: Session, version_id: str, job_id: Optional[str] = None) -> Dict[str, Any]:
    # Check cache first
    existing = db.query(Profile).filter(Profile.dataset_version_id == version_id).first()
    if existing:
        if job_id:
            jobs.mark_job_done(job_id)
        return json.loads(existing.results_json)

    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
    if not version:
        raise ValueError(f"Version {version_id} not found.")

    if job_id:
        jobs.update_job_progress(job_id, 10, "running")

    df = read_version_df(version)
    total_rows = len(df)
    columns_profile = {}

    for idx, col in enumerate(df.columns):
        if job_id and jobs.is_job_cancelled(job_id):
            return {}

        series = df[col]
        null_count = int(series.isna().sum())
        null_pct = round((null_count / total_rows) * 100, 2) if total_rows > 0 else 0
        distinct_count = int(series.nunique(dropna=True))
        semantic = detect_semantic_type(series)

        col_data = {
            "name": col,
            "dtype": str(series.dtype),
            "semantic_type": semantic["type"],
            "semantic_confidence": semantic["confidence"],
            "null_count": null_count,
            "null_pct": null_pct,
            "distinct_count": distinct_count,
            "distinct_pct": round((distinct_count / total_rows) * 100, 2) if total_rows > 0 else 0,
        }

        # Numeric stats
        if pd.api.types.is_numeric_dtype(series):
            valid_nums = series.dropna()
            if len(valid_nums) > 0:
                col_data.update({
                    "min": float(valid_nums.min()),
                    "max": float(valid_nums.max()),
                    "mean": round(float(valid_nums.mean()), 4),
                    "std": round(float(valid_nums.std()), 4) if len(valid_nums) > 1 else 0.0,
                    "median": float(valid_nums.median()),
                    "p25": float(valid_nums.quantile(0.25)),
                    "p75": float(valid_nums.quantile(0.75)),
                })
                # Histogram (10 bins)
                counts, bin_edges = np.histogram(valid_nums, bins=10)
                col_data["histogram"] = [
                    {"bin_start": round(float(bin_edges[i]), 2), "bin_end": round(float(bin_edges[i+1]), 2), "count": int(counts[i])}
                    for i in range(len(counts))
                ]
        else:
            # Top-N frequent values
            top_vals = series.value_counts(dropna=True).head(5).to_dict()
            col_data["top_values"] = [{"value": str(k), "count": int(v)} for k, v in top_vals.items()]

        columns_profile[col] = col_data
        if job_id:
            progress = int(10 + (idx + 1) / len(df.columns) * 80)
            jobs.update_job_progress(job_id, progress)

    profile_payload = {
        "version_id": version_id,
        "row_count": total_rows,
        "col_count": len(df.columns),
        "columns": columns_profile
    }

    # Save to DB cache
    profile_record = Profile(
        dataset_version_id=version_id,
        results_json=json.dumps(profile_payload, default=str)
    )
    db.add(profile_record)
    db.commit()

    if job_id:
        jobs.mark_job_done(job_id)

    return profile_payload
