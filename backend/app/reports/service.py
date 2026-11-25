import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException

from backend.app.platform.config import REPORTS_DIR
from backend.app.platform.models import Report, Dataset, DatasetVersion, Assessment, Issue, AuditEvent
from backend.app.platform import audit, jobs

def generate_report_content(
    db: Session,
    dataset_id: str,
    version_ids: List[str],
    sections: List[str]
) -> Dict[str, Any]:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Dataset not found", "details": {}}})

    versions = db.query(DatasetVersion).filter(DatasetVersion.id.in_(version_ids)).order_by(DatasetVersion.number.asc()).all()
    latest_v = versions[-1] if versions else None

    # Assessment & Scores
    assessment_data = None
    issues_data = []
    if latest_v:
        assessment = db.query(Assessment).filter(Assessment.dataset_version_id == latest_v.id).order_by(Assessment.created_at.desc()).first()
        if assessment:
            assessment_data = {
                "overall_score": assessment.overall,
                "dimension_scores": json.loads(assessment.scores_json)
            }
            issues = db.query(Issue).filter(Issue.assessment_id == assessment.id).all()
            issues_data = [
                {
                    "type": i.type,
                    "dimension": i.dimension,
                    "column": i.column,
                    "severity": i.severity,
                    "affected_count": i.affected_count,
                    "why_text": i.why_text,
                    "status": i.status
                }
                for i in issues
            ]

    # Audit events / Actions taken
    audit_events = db.query(AuditEvent).order_by(AuditEvent.created_at.asc()).all()
    actions_taken = [
        {
            "action": ev.action,
            "entity": ev.entity_type,
            "timestamp": ev.created_at.isoformat() if ev.created_at else None,
            "details": json.loads(ev.after_json) if ev.after_json else {}
        }
        for ev in audit_events
    ]

    report_data = {
        "dataset_name": dataset.name,
        "dataset_id": dataset.id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "versions_included": [{"number": v.number, "kind": v.kind, "rows": v.row_count, "cols": v.col_count, "checksum": v.checksum[:12]} for v in versions],
        "assessment": assessment_data,
        "issues": issues_data,
        "actions_taken": actions_taken,
        "sections": sections
    }
    return report_data

def build_html_report(data: dict) -> str:
    """Renders a self-contained, audit-ready HTML report."""
    issues_html = "".join(f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #e2e8f0;"><span style="text-transform: uppercase; font-size: 11px; padding: 2px 6px; border-radius: 4px; background: {'#fee2e2' if i['severity']=='high' else '#fef3c7'}; color: {'#991b1b' if i['severity']=='high' else '#92400e'}">{i['severity']}</span></td>
            <td style="padding: 8px; border: 1px solid #e2e8f0; font-weight: 500;">{i['dimension']}</td>
            <td style="padding: 8px; border: 1px solid #e2e8f0; font-family: monospace;">{i['column'] or 'ALL'}</td>
            <td style="padding: 8px; border: 1px solid #e2e8f0;">{i['affected_count']}</td>
            <td style="padding: 8px; border: 1px solid #e2e8f0;">{i['why_text']}</td>
            <td style="padding: 8px; border: 1px solid #e2e8f0;">{i['status']}</td>
        </tr>
    """ for i in data.get("issues", []))

    actions_html = "".join(f"""
        <li style="margin-bottom: 6px; color: #475569;">
            <strong>{a['action']}</strong> on <em>{a['entity']}</em> at <small>{a['timestamp']}</small>
        </li>
    """ for a in data.get("actions_taken", []))

    dim_scores = data.get("assessment", {}).get("dimension_scores", {}) if data.get("assessment") else {}
    scores_html = "".join(f"""
        <div style="background: #f8fafc; padding: 12px; border-radius: 6px; text-align: center; border: 1px solid #e2e8f0;">
            <div style="font-size: 12px; color: #64748b; text-transform: capitalize;">{dim}</div>
            <div style="font-size: 20px; font-weight: 700; color: #0f172a;">{score}</div>
        </div>
    """ for dim, score in dim_scores.items())

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Data Quality Audit Report - {data['dataset_name']}</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; background: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
        .card {{ background: white; border-radius: 8px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 24px; border: 1px solid #e2e8f0; }}
        h1, h2, h3 {{ margin-top: 0; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    </style>
</head>
<body>
    <div style="max-width: 1000px; margin: 0 auto;">
        <div class="card" style="border-left: 6px solid #2563eb;">
            <h1 style="margin-bottom: 4px;">Data Quality Workbench — Audit Report</h1>
            <p style="color: #64748b; margin-top: 0;">Dataset: <strong>{data['dataset_name']}</strong> (ID: {data['dataset_id']})</p>
            <p style="font-size: 13px; color: #94a3b8;">Generated on: {data['generated_at']}</p>
        </div>

        <div class="card">
            <h2>Quality Assessment Summary</h2>
            <div style="font-size: 36px; font-weight: 800; color: #2563eb; margin-bottom: 16px;">
                Overall Score: {(data.get('assessment') or {}).get('overall_score', 'N/A')} / 100
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px;">
                {scores_html}
            </div>
        </div>

        <div class="card">
            <h2>Identified Issues ({len(data.get('issues', []))})</h2>
            <table>
                <thead>
                    <tr style="background: #f1f5f9; text-align: left;">
                        <th style="padding: 8px; border: 1px solid #e2e8f0;">Severity</th>
                        <th style="padding: 8px; border: 1px solid #e2e8f0;">Dimension</th>
                        <th style="padding: 8px; border: 1px solid #e2e8f0;">Column</th>
                        <th style="padding: 8px; border: 1px solid #e2e8f0;">Affected</th>
                        <th style="padding: 8px; border: 1px solid #e2e8f0;">Evidence</th>
                        <th style="padding: 8px; border: 1px solid #e2e8f0;">Status</th>
                    </tr>
                </thead>
                <tbody>
                    {issues_html}
                </tbody>
            </table>
        </div>

        <div class="card">
            <h2>Audit Trail & Actions Taken</h2>
            <ul>
                {actions_html}
            </ul>
        </div>
    </div>
</body>
</html>
"""
    return html

def create_report(
    db: Session,
    dataset_id: str,
    version_ids: List[str],
    sections: List[str],
    report_format: str = "html",
    user_id: Optional[str] = None
) -> Report:
    data = generate_report_content(db, dataset_id, version_ids, sections)
    html_content = build_html_report(data)

    report_id = secrets.token_hex(8)
    file_name = f"report_{dataset_id}_{report_id}.{report_format}"
    file_path = REPORTS_DIR / file_name

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    share_token = secrets.token_urlsafe(16)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    report = Report(
        id=report_id,
        dataset_id=dataset_id,
        version_ids=json.dumps(version_ids),
        sections=json.dumps(sections),
        format=report_format,
        file_path=str(file_path),
        share_token=share_token,
        expires_at=expires_at
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    audit.record(
        db=db,
        actor_id=user_id,
        action="REPORT_GENERATED",
        entity_type="report",
        entity_id=report.id,
        after={"format": report_format, "share_token": share_token}
    )

    return report
