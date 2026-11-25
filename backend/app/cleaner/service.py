import json
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
from sqlalchemy.orm import Session
from fastapi import HTTPException

from backend.app.platform.models import Issue, Action, DatasetVersion, Dataset, Assessment
from backend.app.platform.versioning import read_version_df, save_immutable_version_df
from backend.app.platform import audit, jobs

def get_issue_recommendations(db: Session, issue_id: str) -> List[Dict[str, Any]]:
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Issue not found", "details": {}}})
    
    recs = []
    if issue.type == "duplicate":
        recs.append({
            "type": "drop_duplicates",
            "title": "Drop Duplicate Rows",
            "description": "Removes identical duplicate rows, keeping only the first occurrence.",
            "default_config": {"keep": "first"}
        })
    elif issue.type == "disguised_null":
        recs.append({
            "type": "null_out",
            "title": "Standardize to NULL",
            "description": f"Replace disguised values like 'N/A', '-', '999' in '{issue.column}' with standard NULL.",
            "default_config": {"column": issue.column, "disguised_values": ["N/A", "-", "999", "null", "none"]}
        })
        recs.append({
            "type": "fill_null",
            "title": "Impute Missing Values",
            "description": f"Fill missing or disguised null values in '{issue.column}' with median/mean or constant.",
            "default_config": {"column": issue.column, "strategy": "median"}
        })
    elif issue.type == "inconsistent_category":
        recs.append({
            "type": "standardise_category",
            "title": "Standardize Casing",
            "description": f"Standardize all values in '{issue.column}' to Title Case (e.g. 'Mumbai').",
            "default_config": {"column": issue.column, "case": "title"}
        })
    elif issue.type == "invalid_format":
        recs.append({
            "type": "null_out_invalid",
            "title": "Set Invalid Format to NULL",
            "description": f"Clear malformed values in '{issue.column}' so they don't break downstream pipelines.",
            "default_config": {"column": issue.column}
        })
    else:
        recs.append({
            "type": "trim_whitespace",
            "title": "Trim Whitespace",
            "description": "Strip leading and trailing whitespace from string columns.",
            "default_config": {"column": issue.column}
        })

    return recs

def apply_transformation_df(df: pd.DataFrame, action_type: str, config: dict) -> Tuple[pd.DataFrame, int]:
    new_df = df.copy()
    rows_affected = 0
    
    if action_type == "drop_duplicates":
        keep = config.get("keep", "first")
        initial_len = len(new_df)
        new_df = new_df.drop_duplicates(keep=keep).reset_index(drop=True)
        rows_affected = initial_len - len(new_df)

    elif action_type == "standardise_category":
        col = config.get("column")
        case = config.get("case", "title")
        if col in new_df.columns:
            def transform_val(val):
                if pd.isna(val):
                    return val
                s = str(val).strip()
                return s.title() if case == "title" else (s.upper() if case == "upper" else s.lower())

            orig = new_df[col].astype(str)
            new_df[col] = new_df[col].apply(transform_val)
            rows_affected = int((orig != new_df[col].astype(str)).sum())

    elif action_type in ["null_out", "null_out_invalid"]:
        col = config.get("column")
        disguised = set(d.lower() for d in config.get("disguised_values", ["n/a", "na", "-", "999", "null", "none", "", "?"]))
        if col in new_df.columns:
            mask = new_df[col].astype(str).str.strip().str.lower().isin(disguised)
            if "age" in col.lower() and pd.api.types.is_numeric_dtype(new_df[col]):
                mask = mask | (new_df[col] == 999)
            rows_affected = int(mask.sum())
            new_df.loc[mask, col] = None

    elif action_type == "fill_null":
        col = config.get("column")
        strategy = config.get("strategy", "median")
        if col in new_df.columns:
            null_mask = new_df[col].isna() | new_df[col].astype(str).str.strip().str.lower().isin(["n/a", "-", "999"])
            rows_affected = int(null_mask.sum())
            if pd.api.types.is_numeric_dtype(new_df[col]):
                clean_vals = new_df.loc[~null_mask, col].dropna()
                fill_val = clean_vals.median() if strategy == "median" else clean_vals.mean()
                new_df.loc[null_mask, col] = fill_val
            else:
                new_df.loc[null_mask, col] = "Unknown"

    return new_df, rows_affected

def propose_action(
    db: Session,
    issue_id: Optional[str],
    version_id: str,
    action_type: str,
    config: dict,
    user_id: Optional[str] = None
) -> Action:
    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Version not found", "details": {}}})
    
    df = read_version_df(version)
    _, rows_affected = apply_transformation_df(df, action_type, config)

    action = Action(
        issue_id=issue_id,
        dataset_version_id=version_id,
        type=action_type,
        config_json=json.dumps(config),
        status="proposed",
        rows_affected=rows_affected,
        proposed_by=user_id
    )
    db.add(action)
    db.commit()
    db.refresh(action)

    audit.record(
        db=db,
        actor_id=user_id,
        action="ACTION_PROPOSED",
        entity_type="action",
        entity_id=action.id,
        after={"type": action_type, "config": config, "rows_affected": rows_affected}
    )

    return action

def preview_action(db: Session, action_id: str) -> Dict[str, Any]:
    action = db.query(Action).filter(Action.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Action not found", "details": {}}})
    
    version = db.query(DatasetVersion).filter(DatasetVersion.id == action.dataset_version_id).first()
    df_before = read_version_df(version)
    config = json.loads(action.config_json)
    df_after, rows_affected = apply_transformation_df(df_before, action.type, config)

    return {
        "action_id": action.id,
        "type": action.type,
        "rows_affected": rows_affected,
        "initial_row_count": len(df_before),
        "preview_row_count": len(df_after),
        "before_sample": df_before.head(10).where(pd.notnull(df_before.head(10)), None).to_dict(orient="records"),
        "after_sample": df_after.head(10).where(pd.notnull(df_after.head(10)), None).to_dict(orient="records")
    }

def approve_action(db: Session, action_id: str, approver_id: str) -> Action:
    action = db.query(Action).filter(Action.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Action not found", "details": {}}})
    
    action.status = "approved"
    action.approved_by = approver_id
    db.commit()

    audit.record(
        db=db,
        actor_id=approver_id,
        action="ACTION_APPROVED",
        entity_type="action",
        entity_id=action.id,
        after={"status": "approved"}
    )
    return action

def apply_action_new_version(
    db: Session,
    action_id: str,
    user_id: str,
    job_id: Optional[str] = None
) -> DatasetVersion:
    action = db.query(Action).filter(Action.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Action not found", "details": {}}})
    
    if action.status != "approved":
        raise HTTPException(status_code=400, detail={"error": {"code": "NOT_APPROVED", "message": "Action must be approved by an Approver before applying", "details": {}}})

    if job_id:
        jobs.update_job_progress(job_id, 20, "running")

    parent_version = db.query(DatasetVersion).filter(DatasetVersion.id == action.dataset_version_id).first()
    dataset = db.query(Dataset).filter(Dataset.id == parent_version.dataset_id).first()

    # Read parent data without mutating
    df_parent = read_version_df(parent_version)
    config = json.loads(action.config_json)
    df_cleaned, rows_affected = apply_transformation_df(df_parent, action.type, config)

    new_version_number = parent_version.number + 1

    if job_id:
        jobs.update_job_progress(job_id, 60)

    # Save new immutable parquet file
    parquet_path, row_count, col_count, checksum = save_immutable_version_df(
        dataset_id=dataset.id,
        version_number=new_version_number,
        df=df_cleaned
    )

    new_version = DatasetVersion(
        dataset_id=dataset.id,
        number=new_version_number,
        kind="cleaned",
        parent_version_id=parent_version.id,
        parquet_path=parquet_path,
        row_count=row_count,
        col_count=col_count,
        checksum=checksum,
        created_by=user_id
    )
    db.add(new_version)
    db.flush()

    dataset.latest_version_id = new_version.id
    action.status = "applied"
    action.result_version_id = new_version.id
    db.commit()

    audit.record(
        db=db,
        actor_id=user_id,
        action="ACTION_APPLIED",
        entity_type="dataset_version",
        entity_id=new_version.id,
        before={"version": parent_version.number, "rows": parent_version.row_count},
        after={"version": new_version.number, "rows": row_count, "action_type": action.type}
    )

    if job_id:
        jobs.mark_job_done(job_id)

    return new_version

def restore_version(db: Session, version_id: str, user_id: str) -> DatasetVersion:
    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Version not found", "details": {}}})

    dataset = db.query(Dataset).filter(Dataset.id == version.dataset_id).first()
    prev_latest = dataset.latest_version_id
    dataset.latest_version_id = version.id
    db.commit()

    audit.record(
        db=db,
        actor_id=user_id,
        action="VERSION_RESTORED",
        entity_type="dataset",
        entity_id=dataset.id,
        before={"latest_version_id": prev_latest},
        after={"latest_version_id": version.id, "version_number": version.number}
    )

    return version
