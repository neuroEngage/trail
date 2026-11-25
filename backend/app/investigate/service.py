import io
import json
import pandas as pd
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException

from backend.app.platform.models import Issue, IssueRecord, Assessment, DatasetVersion
from backend.app.platform.versioning import read_version_df
from backend.app.platform import audit

def get_issue_detail(db: Session, issue_id: str) -> Dict[str, Any]:
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Issue not found", "details": {}}})
    
    assessment = db.query(Assessment).filter(Assessment.id == issue.assessment_id).first()
    return {
        "id": issue.id,
        "assessment_id": issue.assessment_id,
        "dataset_version_id": assessment.dataset_version_id if assessment else None,
        "type": issue.type,
        "dimension": issue.dimension,
        "column": issue.column,
        "severity": issue.severity,
        "affected_count": issue.affected_count,
        "why_text": issue.why_text,
        "status": issue.status,
        "created_at": issue.created_at.isoformat() if issue.created_at else None
    }

def get_issue_records(
    db: Session,
    issue_id: str,
    page: int = 1,
    page_size: int = 50
) -> Dict[str, Any]:
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Issue not found", "details": {}}})
    
    assessment = db.query(Assessment).filter(Assessment.id == issue.assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Assessment not found", "details": {}}})
    
    version = db.query(DatasetVersion).filter(DatasetVersion.id == assessment.dataset_version_id).first()
    df = read_version_df(version)

    records_query = db.query(IssueRecord).filter(IssueRecord.issue_id == issue_id)
    total_records = records_query.count()
    
    offset = (page - 1) * page_size
    records = records_query.offset(offset).limit(page_size).all()

    # Extract the full row data from df for each affected row index
    rows_data = []
    for r in records:
        row_dict = {}
        if 0 <= r.row_index < len(df):
            row_dict = df.iloc[r.row_index].where(pd.notnull(df.iloc[r.row_index]), None).to_dict()
        row_dict["__row_index__"] = r.row_index
        row_dict["__flagged_column__"] = r.column
        row_dict["__flagged_value__"] = r.value
        rows_data.append(row_dict)

    return {
        "issue_id": issue_id,
        "page": page,
        "page_size": page_size,
        "total": total_records,
        "flagged_column": issue.column,
        "records": rows_data
    }

def update_issue_status(
    db: Session,
    issue_id: str,
    status_val: str,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    if status_val not in ["open", "valid", "ignored"]:
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_STATUS", "message": "Status must be open, valid, or ignored", "details": {}}})

    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Issue not found", "details": {}}})

    old_status = issue.status
    issue.status = status_val
    db.commit()

    # Recalculate assessment score
    assessment = db.query(Assessment).filter(Assessment.id == issue.assessment_id).first()
    if assessment:
        # Remaining open issues
        open_issues = db.query(Issue).filter(Issue.assessment_id == assessment.id, Issue.status == "open").all()
        version = db.query(DatasetVersion).filter(DatasetVersion.id == assessment.dataset_version_id).first()
        total_rows = version.row_count if version and version.row_count > 0 else 1000
        total_cells = total_rows * (version.col_count if version and version.col_count > 0 else 10)

        dim_fails = {"completeness": 0, "validity": 0, "uniqueness": 0, "consistency": 0, "accuracy": 0, "timeliness": 0}
        for oi in open_issues:
            dim_fails[oi.dimension] = dim_fails.get(oi.dimension, 0) + oi.affected_count

        dim_scores = {}
        for dim, fails in dim_fails.items():
            base = total_cells if dim in ["completeness", "validity"] else total_rows
            dim_scores[dim] = round(max(0.0, min(100.0, 100.0 * (1.0 - (fails / base)))), 1)
        
        weights = {"completeness": 0.25, "validity": 0.25, "uniqueness": 0.2, "consistency": 0.2, "accuracy": 0.1, "timeliness": 0.0}
        assessment.overall = round(sum(dim_scores[d] * weights.get(d, 0.15) for d in dim_scores), 1)
        assessment.scores_json = json.dumps(dim_scores)
        db.commit()

    audit.record(
        db=db,
        actor_id=user_id,
        action="UPDATE_ISSUE_STATUS",
        entity_type="issue",
        entity_id=issue.id,
        before={"status": old_status},
        after={"status": status_val}
    )

    return {
        "id": issue.id,
        "status": issue.status,
        "assessment_id": assessment.id if assessment else None,
        "new_overall_score": assessment.overall if assessment else None
    }

def export_issue_rows_csv(db: Session, issue_id: str) -> str:
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Issue not found", "details": {}}})
    
    assessment = db.query(Assessment).filter(Assessment.id == issue.assessment_id).first()
    version = db.query(DatasetVersion).filter(DatasetVersion.id == assessment.dataset_version_id).first()
    df = read_version_df(version)

    records = db.query(IssueRecord).filter(IssueRecord.issue_id == issue_id).all()
    row_indices = [r.row_index for r in records if 0 <= r.row_index < len(df)]
    
    sub_df = df.iloc[row_indices].copy()
    sub_df.insert(0, "row_index", row_indices)
    
    output = io.StringIO()
    sub_df.to_csv(output, index=False)
    return output.getvalue()
