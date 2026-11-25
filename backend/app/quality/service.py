import json
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.app.platform.models import DatasetVersion, Assessment, Issue, IssueRecord, Rule, Dataset
from backend.app.platform.versioning import read_version_df
from backend.app.platform import jobs

DISGUISED_NULL_VALUES = {"n/a", "na", "-", "--", "null", "none", "", "?", "unknown", "999"}
EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")

def run_quality_assessment(
    db: Session,
    version_id: str,
    job_id: Optional[str] = None
) -> Assessment:
    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
    if not version:
        raise ValueError(f"Version {version_id} not found.")

    if job_id:
        jobs.update_job_progress(job_id, 10, "running")

    df = read_version_df(version)
    total_rows = len(df)
    total_cols = len(df.columns)
    total_cells = total_rows * total_cols if total_rows > 0 and total_cols > 0 else 1

    # Load custom rules for dataset
    rules = db.query(Rule).filter(Rule.dataset_id == version.dataset_id, Rule.enabled == True).all()

    # Track failing counts per dimension
    dimension_failures = {
        "completeness": 0,
        "validity": 0,
        "uniqueness": 0,
        "consistency": 0,
        "accuracy": 0,
        "timeliness": 0
    }

    detected_issues: List[Dict[str, Any]] = []

    # 1. Uniqueness check: Duplicate rows
    dup_mask = df.duplicated(keep=False)
    dup_count = int(dup_mask.sum())
    if dup_count > 0:
        dup_rows = df[dup_mask].index.tolist()
        dimension_failures["uniqueness"] += dup_count
        detected_issues.append({
            "type": "duplicate",
            "dimension": "uniqueness",
            "column": "ALL",
            "severity": "high",
            "affected_count": dup_count,
            "why_text": f"Found {dup_count} duplicated rows across all columns.",
            "records": [{"row_index": r, "column": "ALL", "value": "Duplicate row"} for r in dup_rows]
        })

    if job_id:
        jobs.update_job_progress(job_id, 30)

    # 2. Check each column for disguised nulls, invalid format, outliers, inconsistent categories
    for col in df.columns:
        series = df[col]
        str_series = series.astype(str).str.strip().str.lower()

        # A. Disguised nulls (Completeness)
        disguised_mask = str_series.isin(DISGUISED_NULL_VALUES) | series.isna()
        # For numeric age columns, 999 is often disguised null
        if "age" in col.lower() and pd.api.types.is_numeric_dtype(series):
            disguised_mask = disguised_mask | (series == 999)

        disguised_count = int(disguised_mask.sum())
        if disguised_count > 0:
            flagged_rows = df[disguised_mask].index.tolist()
            dimension_failures["completeness"] += disguised_count
            detected_issues.append({
                "type": "disguised_null",
                "dimension": "completeness",
                "column": col,
                "severity": "medium",
                "affected_count": disguised_count,
                "why_text": f"Found {disguised_count} disguised or missing null values (e.g. 'N/A', '-', '999') in column '{col}'.",
                "records": [{"row_index": r, "column": col, "value": str(df.at[r, col])} for r in flagged_rows]
            })

        # B. Invalid format (Validity)
        if "email" in col.lower():
            invalid_email_rows = []
            for r_idx, val in series.items():
                if pd.notna(val) and not str(val).lower() in DISGUISED_NULL_VALUES:
                    if not EMAIL_REGEX.match(str(val).strip()):
                        invalid_email_rows.append(r_idx)
            
            if invalid_email_rows:
                count = len(invalid_email_rows)
                dimension_failures["validity"] += count
                detected_issues.append({
                    "type": "invalid_format",
                    "dimension": "validity",
                    "column": col,
                    "severity": "high",
                    "affected_count": count,
                    "why_text": f"Found {count} invalid email format entries in column '{col}'.",
                    "records": [{"row_index": r, "column": col, "value": str(df.at[r, col])} for r in invalid_email_rows]
                })

        # C. Outliers (Accuracy / Validity)
        if pd.api.types.is_numeric_dtype(series) and series.nunique() > 10:
            valid_nums = series.dropna()
            if len(valid_nums) > 10:
                q25 = valid_nums.quantile(0.25)
                q75 = valid_nums.quantile(0.75)
                iqr = q75 - q25
                upper_bound = q75 + (1.5 * iqr)
                lower_bound = q25 - (1.5 * iqr)

                # Filter out values that are disguised nulls like 999 if age
                outlier_mask = (series > upper_bound) | (series < lower_bound)
                if "age" in col.lower():
                    outlier_mask = outlier_mask & (series != 999)

                outlier_rows = df[outlier_mask].index.tolist()
                if outlier_rows:
                    count = len(outlier_rows)
                    dimension_failures["accuracy"] += count
                    detected_issues.append({
                        "type": "outlier",
                        "dimension": "accuracy",
                        "column": col,
                        "severity": "medium",
                        "affected_count": count,
                        "why_text": f"Found {count} statistical outliers exceeding IQR threshold [{round(lower_bound, 2)}, {round(upper_bound, 2)}] in '{col}'.",
                        "records": [{"row_index": r, "column": col, "value": str(df.at[r, col])} for r in outlier_rows]
                    })

        # D. Inconsistent Category (Consistency)
        if not pd.api.types.is_numeric_dtype(series) and series.nunique() > 1:
            clean_strs = series.dropna().astype(str).str.strip()
            # Group by lowercase and check for multiple distinct casings
            grouped = clean_strs.groupby(clean_strs.str.lower()).nunique()
            inconsistent_keys = grouped[grouped > 1].index.tolist()
            if inconsistent_keys:
                inconsistent_rows = []
                for r_idx, val in series.items():
                    if pd.notna(val) and str(val).strip().lower() in inconsistent_keys:
                        # Count variant casing rows
                        inconsistent_rows.append(r_idx)
                
                if inconsistent_rows:
                    count = len(inconsistent_rows)
                    dimension_failures["consistency"] += count
                    detected_issues.append({
                        "type": "inconsistent_category",
                        "dimension": "consistency",
                        "column": col,
                        "severity": "medium",
                        "affected_count": count,
                        "why_text": f"Found {count} inconsistent case variations for categories in '{col}' (e.g. variants of {inconsistent_keys[:3]}).",
                        "records": [{"row_index": r, "column": col, "value": str(df.at[r, col])} for r in inconsistent_rows]
                    })

    if job_id:
        jobs.update_job_progress(job_id, 65)

    # 3. Custom Rules & Consistency / Timeliness (e.g., end_date >= signup_date)
    # Check default golden rule: end_date >= signup_date if both exist
    if "signup_date" in df.columns and "end_date" in df.columns:
        date_viol_rows = []
        for r_idx in range(len(df)):
            try:
                s_val = pd.to_datetime(df.at[r_idx, "signup_date"])
                e_val = pd.to_datetime(df.at[r_idx, "end_date"])
                if pd.notna(s_val) and pd.notna(e_val) and e_val < s_val:
                    date_viol_rows.append(r_idx)
            except Exception:
                continue

        if date_viol_rows:
            count = len(date_viol_rows)
            dimension_failures["consistency"] += count
            detected_issues.append({
                "type": "rule_violation",
                "dimension": "consistency",
                "column": "end_date",
                "severity": "high",
                "affected_count": count,
                "why_text": f"Rule violation 'end_date >= signup_date': found {count} records where end date precedes signup date.",
                "records": [{"row_index": r, "column": "end_date", "value": f"end={df.at[r, 'end_date']} < start={df.at[r, 'signup_date']}"} for r in date_viol_rows]
            })

    # Evaluate any additional DB stored custom rules
    for r in rules:
        try:
            expr = r.expression
            # DuckDB or pandas eval for safe expression execution
            cond_mask = df.eval(expr)
            viol_mask = ~cond_mask
            viol_count = int(viol_mask.sum())
            if viol_count > 0:
                flagged = df[viol_mask].index.tolist()
                dim = r.dimension or "consistency"
                dimension_failures[dim] = dimension_failures.get(dim, 0) + viol_count
                detected_issues.append({
                    "type": "custom_rule_violation",
                    "dimension": dim,
                    "column": "custom",
                    "severity": r.severity or "high",
                    "affected_count": viol_count,
                    "why_text": f"Custom rule '{r.name}' failed on expression '{r.expression}' ({viol_count} rows).",
                    "records": [{"row_index": i, "column": "custom", "value": "Violates expression"} for i in flagged[:100]]
                })
        except Exception:
            continue

    if job_id:
        jobs.update_job_progress(job_id, 85)

    # 4. Calculate Scores per D12: score = 100 * (1 - failures / total)
    dim_scores = {}
    weights = {"completeness": 0.25, "validity": 0.25, "uniqueness": 0.2, "consistency": 0.2, "accuracy": 0.1, "timeliness": 0.0}
    
    for dim, fails in dimension_failures.items():
        base = total_cells if dim in ["completeness", "validity"] else total_rows
        base = max(1, base)
        score = max(0.0, min(100.0, 100.0 * (1.0 - (fails / base))))
        dim_scores[dim] = round(score, 1)

    overall_score = round(sum(dim_scores[d] * weights.get(d, 0.15) for d in dim_scores), 1)

    # Sort issues by severity (critical > high > medium > low), then affected_count desc
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    detected_issues.sort(key=lambda x: (severity_order.get(x["severity"], 2), -x["affected_count"]))

    # Save Assessment to DB
    assessment = Assessment(
        dataset_version_id=version_id,
        scores_json=json.dumps(dim_scores),
        overall=overall_score,
        created_at=datetime.now(timezone.utc)
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)

    # Save Issues and IssueRecords
    for iss in detected_issues:
        issue_model = Issue(
            assessment_id=assessment.id,
            type=iss["type"],
            dimension=iss["dimension"],
            column=iss["column"],
            severity=iss["severity"],
            affected_count=iss["affected_count"],
            why_text=iss["why_text"],
            status="open"
        )
        db.add(issue_model)
        db.commit()
        db.refresh(issue_model)

        for rec in iss["records"][:500]:  # store up to 500 for investigation view
            ir = IssueRecord(
                issue_id=issue_model.id,
                row_index=rec["row_index"],
                column=rec["column"],
                value=str(rec["value"])
            )
            db.add(ir)
        db.commit()

    if job_id:
        jobs.mark_job_done(job_id)

    return assessment
