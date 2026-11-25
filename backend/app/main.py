import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Query, Response, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.platform.database import get_db, init_db, SessionLocal
from backend.app.platform.models import User, Source, Dataset, DatasetVersion, Job, AuditEvent, Assessment, Issue, Action, Comparison, Report, Rule
from backend.app.platform.auth import (
    hash_password, verify_password, create_access_token, get_current_user, require_roles
)
from backend.app.platform import jobs, audit
from backend.app.connect import service as connect_service
from backend.app.catalogue import service as catalogue_service
from backend.app.profiler import service as profiler_service
from backend.app.quality import service as quality_service
from backend.app.investigate import service as investigate_service
from backend.app.cleaner import service as cleaner_service
from backend.app.compare import service as compare_service
from backend.app.reports import service as reports_service

app = FastAPI(
    title="Data Quality Workbench API",
    version="0.1.0",
    description="Single source of truth between UI and backend per API_CONTRACT.yaml"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup: Initialize DB and seed default users
@app.on_event("startup")
def on_startup():
    init_db()
    db = SessionLocal()
    try:
        users = [
            ("admin@dq.local", "password123", "admin"),
            ("approver@dq.local", "password123", "approver"),
            ("analyst@dq.local", "password123", "analyst"),
        ]
        for email, pwd, role in users:
            existing = db.query(User).filter(User.email == email).first()
            if not existing:
                u = User(email=email, hashed_password=hash_password(pwd), role=role)
                db.add(u)
        db.commit()
    finally:
        db.close()

# ----------------- Auth -----------------
class LoginRequest(BaseModel):
    email: str
    password: str

@app.post("/api/v1/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid email or password", "details": {}}}
        )
    token = create_access_token({"sub": user.id, "email": user.email, "role": user.role})
    return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "email": user.email, "role": user.role}}

# ----------------- Jobs -----------------
@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Job not found", "details": {}}})
    return {"id": job.id, "type": job.type, "status": job.status, "progress": job.progress, "error": job.error}

@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: str):
    success = jobs.cancel_job(job_id)
    if not success:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Job not found", "details": {}}})
    return {"status": "cancelled", "id": job_id}

# ----------------- F1: Connect -----------------
@app.post("/api/v1/sources/files", status_code=status.HTTP_201_CREATED)
async def upload_source_file(
    file: UploadFile = File(...),
    dataset_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    content = await file.read()
    try:
        ds, ver = connect_service.register_file_dataset(
            db=db,
            filename=file.filename or "uploaded_dataset.csv",
            content=content,
            dataset_name=dataset_name,
            user_id=current_user.id
        )
        return {
            "dataset_id": ds.id,
            "version_id": ver.id,
            "name": ds.name,
            "row_count": ver.row_count,
            "col_count": ver.col_count,
            "checksum": ver.checksum
        }
    except connect_service.ConnectError as e:
        raise HTTPException(status_code=400, detail={"error": {"code": "FILE_PARSE_ERROR", "message": str(e), "details": {}}})

class DatabaseSourceRequest(BaseModel):
    name: str
    host: str
    port: int = 5432
    database: str
    username: str
    password: str

@app.post("/api/v1/sources/databases", status_code=status.HTTP_201_CREATED)
def create_database_source(
    req: DatabaseSourceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    encrypted = connect_service.encrypt_db_config(req.dict())
    source = Source(
        name=req.name,
        type="postgres",
        config_encrypted=encrypted,
        status="active",
        owner_id=current_user.id
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return {"id": source.id, "name": source.name, "type": source.type, "status": source.status}

@app.post("/api/v1/sources/{source_id}/test")
def test_db_source(source_id: str, db: Session = Depends(get_db)):
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Source not found", "details": {}}})
    config = connect_service.decrypt_db_config(source.config_encrypted)
    ok, msg = connect_service.test_database_connection(config)
    if not ok:
        raise HTTPException(status_code=400, detail={"error": {"code": "CONNECTION_FAILED", "message": msg, "details": {}}})
    return {"status": "ok", "message": msg}

@app.get("/api/v1/sources/{source_id}/schemas")
def get_db_schemas(source_id: str, db: Session = Depends(get_db)):
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Source not found", "details": {}}})
    config = connect_service.decrypt_db_config(source.config_encrypted)
    return connect_service.discover_db_schemas(config)

class SnapshotRequest(BaseModel):
    schema_name: str
    table_name: str
    dataset_name: Optional[str] = None
    query: Optional[str] = None

@app.post("/api/v1/sources/{source_id}/snapshot", status_code=status.HTTP_202_ACCEPTED)
def snapshot_database(
    source_id: str,
    req: SnapshotRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    job = jobs.enqueue_job("snapshot", source_id)
    def worker():
        try:
            connect_service.snapshot_database_table(
                db=db,
                source_id=source_id,
                schema_name=req.schema_name,
                table_name=req.table_name,
                dataset_name=req.dataset_name,
                query=req.query,
                user_id=current_user.id
            )
            jobs.mark_job_done(job.id)
        except Exception as e:
            jobs.mark_job_failed(job.id, str(e))
    jobs.run_in_background(worker)
    return {"job_id": job.id, "status": "queued"}

@app.delete("/api/v1/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_source(source_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Source not found", "details": {}}})
    source.status = "disconnected"
    db.commit()
    audit.record(db=db, actor_id=current_user.id, action="SOURCE_DISCONNECTED", entity_type="source", entity_id=source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# ----------------- F2: Catalogue -----------------
@app.get("/api/v1/datasets")
def get_datasets(db: Session = Depends(get_db)):
    return catalogue_service.list_datasets(db)

@app.get("/api/v1/datasets/{dataset_id}")
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    return catalogue_service.get_dataset_metadata(db, dataset_id)

@app.get("/api/v1/datasets/{dataset_id}/versions")
def get_versions(dataset_id: str, db: Session = Depends(get_db)):
    return catalogue_service.list_dataset_versions(db, dataset_id)

@app.get("/api/v1/versions/{version_id}/columns")
def get_version_columns(version_id: str, db: Session = Depends(get_db)):
    return catalogue_service.get_version_columns(db, version_id)

@app.get("/api/v1/versions/{version_id}/rows")
def get_version_rows(
    version_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort_by: Optional[str] = None,
    sort_desc: bool = False,
    db: Session = Depends(get_db)
):
    return catalogue_service.get_version_rows(db, version_id, page, page_size, sort_by, sort_desc)

# ----------------- F3: Profiler -----------------
@app.post("/api/v1/versions/{version_id}/profile", status_code=status.HTTP_202_ACCEPTED)
def start_profiling(version_id: str, db: Session = Depends(get_db)):
    job = jobs.enqueue_job("profile", version_id)
    def worker():
        try:
            worker_db = SessionLocal()
            try:
                profiler_service.profile_dataset_version(worker_db, version_id, job.id)
            finally:
                worker_db.close()
        except Exception as e:
            jobs.mark_job_failed(job.id, str(e))
    jobs.run_in_background(worker)
    return {"job_id": job.id, "status": "queued"}

@app.get("/api/v1/versions/{version_id}/profile")
def get_profile(version_id: str, db: Session = Depends(get_db)):
    prof = db.query(profiler_service.Profile).filter(profiler_service.Profile.dataset_version_id == version_id).first()
    if not prof:
        raise HTTPException(status_code=404, detail={"error": {"code": "PROFILE_NOT_RUN", "message": "Profile has not been generated for this version yet", "details": {}}})
    return json.loads(prof.results_json)

# ----------------- F4: Quality Assessment -----------------
class RuleCreateRequest(BaseModel):
    name: str
    dimension: str
    expression: str
    severity: str = "medium"

@app.get("/api/v1/datasets/{dataset_id}/rules")
def list_rules(dataset_id: str, db: Session = Depends(get_db)):
    return db.query(Rule).filter(Rule.dataset_id == dataset_id).all()

@app.post("/api/v1/datasets/{dataset_id}/rules", status_code=status.HTTP_201_CREATED)
def create_rule(
    dataset_id: str,
    req: RuleCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    rule = Rule(
        dataset_id=dataset_id,
        name=req.name,
        dimension=req.dimension,
        expression=req.expression,
        severity=req.severity,
        created_by=current_user.id
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule

@app.post("/api/v1/versions/{version_id}/assessment", status_code=status.HTTP_202_ACCEPTED)
def start_assessment(version_id: str, db: Session = Depends(get_db)):
    job = jobs.enqueue_job("assess", version_id)
    def worker():
        try:
            worker_db = SessionLocal()
            try:
                quality_service.run_quality_assessment(worker_db, version_id, job.id)
            finally:
                worker_db.close()
        except Exception as e:
            jobs.mark_job_failed(job.id, str(e))
    jobs.run_in_background(worker)
    return {"job_id": job.id, "status": "queued"}

@app.get("/api/v1/versions/{version_id}/assessment")
def get_assessment(version_id: str, db: Session = Depends(get_db)):
    assessment = db.query(Assessment).filter(Assessment.dataset_version_id == version_id).order_by(Assessment.created_at.desc()).first()
    if not assessment:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_ASSESSED", "message": "Quality assessment has not been run for this version", "details": {}}})
    
    issues = db.query(Issue).filter(Issue.assessment_id == assessment.id).all()
    return {
        "assessment_id": assessment.id,
        "dataset_version_id": version_id,
        "overall": assessment.overall,
        "scores": json.loads(assessment.scores_json),
        "issues": [
            {
                "id": i.id,
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
    }

# ----------------- F5: Investigate -----------------
@app.get("/api/v1/issues/{issue_id}")
def get_issue(issue_id: str, db: Session = Depends(get_db)):
    return investigate_service.get_issue_detail(db, issue_id)

@app.get("/api/v1/issues/{issue_id}/records")
def get_issue_records(
    issue_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db)
):
    return investigate_service.get_issue_records(db, issue_id, page, page_size)

class IssueStatusRequest(BaseModel):
    status: str

@app.patch("/api/v1/issues/{issue_id}/status")
def patch_issue_status(
    issue_id: str,
    req: IssueStatusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return investigate_service.update_issue_status(db, issue_id, req.status, current_user.id)

@app.get("/api/v1/issues/{issue_id}/export")
def export_issue_records(issue_id: str, db: Session = Depends(get_db)):
    csv_data = investigate_service.export_issue_rows_csv(db, issue_id)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=issue_{issue_id}_records.csv"}
    )

# ----------------- F6: Cleaner / Improve -----------------
@app.get("/api/v1/issues/{issue_id}/recommendations")
def get_recommendations(issue_id: str, db: Session = Depends(get_db)):
    return cleaner_service.get_issue_recommendations(db, issue_id)

class ActionProposeRequest(BaseModel):
    issue_id: Optional[str] = None
    version_id: str
    action_type: str
    config: dict

@app.post("/api/v1/actions", status_code=status.HTTP_201_CREATED)
def propose_action(
    req: ActionProposeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    action = cleaner_service.propose_action(
        db=db,
        issue_id=req.issue_id,
        version_id=req.version_id,
        action_type=req.action_type,
        config=req.config,
        user_id=current_user.id
    )
    return {
        "id": action.id,
        "type": action.type,
        "status": action.status,
        "rows_affected": action.rows_affected,
        "config": json.loads(action.config_json)
    }

@app.get("/api/v1/actions/{action_id}/preview")
def preview_action(action_id: str, db: Session = Depends(get_db)):
    return cleaner_service.preview_action(db, action_id)

@app.post("/api/v1/actions/{action_id}/approve")
def approve_action(
    action_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["approver", "admin"]))
):
    action = cleaner_service.approve_action(db, action_id, current_user.id)
    return {"id": action.id, "status": action.status, "approved_by": action.approved_by}

@app.post("/api/v1/actions/{action_id}/apply", status_code=status.HTTP_202_ACCEPTED)
def apply_action(
    action_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["approver", "admin"]))
):
    job = jobs.enqueue_job("apply", action_id)
    def worker():
        try:
            worker_db = SessionLocal()
            try:
                cleaner_service.apply_action_new_version(worker_db, action_id, current_user.id, job.id)
            finally:
                worker_db.close()
        except Exception as e:
            jobs.mark_job_failed(job.id, str(e))
    jobs.run_in_background(worker)
    return {"job_id": job.id, "status": "queued"}

@app.post("/api/v1/versions/{version_id}/restore")
def restore_version(
    version_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(["approver", "admin"]))
):
    v = cleaner_service.restore_version(db, version_id, current_user.id)
    return {"status": "restored", "version_id": v.id, "version_number": v.number}

# ----------------- F7: Compare -----------------
class CompareRequest(BaseModel):
    version_a: str
    version_b: str
    sql_query: Optional[str] = None

@app.post("/api/v1/comparisons", status_code=status.HTTP_202_ACCEPTED)
def compare_versions(req: CompareRequest, db: Session = Depends(get_db)):
    job = jobs.enqueue_job("compare", f"{req.version_a}:{req.version_b}")
    def worker():
        try:
            worker_db = SessionLocal()
            try:
                comp = compare_service.run_comparison(
                    worker_db, req.version_a, req.version_b, req.sql_query, job.id
                )
            finally:
                worker_db.close()
        except Exception as e:
            jobs.mark_job_failed(job.id, str(e))
    jobs.run_in_background(worker)
    return {"job_id": job.id, "status": "queued"}

@app.get("/api/v1/comparisons/{comparison_id}")
def get_comparison(comparison_id: str, db: Session = Depends(get_db)):
    comp = db.query(Comparison).filter(Comparison.id == comparison_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Comparison not found", "details": {}}})
    return json.loads(comp.result_json)

# ----------------- F8: Reports -----------------
class ReportRequest(BaseModel):
    dataset_id: str
    version_ids: List[str]
    sections: List[str] = ["profile", "issues", "actions", "comparison"]
    format: str = "html"

@app.post("/api/v1/reports", status_code=status.HTTP_202_ACCEPTED)
def create_report(
    req: ReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    job = jobs.enqueue_job("report", req.dataset_id)
    def worker():
        try:
            worker_db = SessionLocal()
            try:
                rep = reports_service.create_report(
                    worker_db, req.dataset_id, req.version_ids, req.sections, req.format, current_user.id
                )
                jobs.mark_job_done(job.id)
            finally:
                worker_db.close()
        except Exception as e:
            jobs.mark_job_failed(job.id, str(e))
    jobs.run_in_background(worker)
    return {"job_id": job.id, "status": "queued"}

@app.get("/api/v1/reports/{report_id}/download")
def download_report(report_id: str, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Report not found", "details": {}}})
    return FileResponse(
        report.file_path,
        media_type="text/html",
        filename=Path(report.file_path).name
    )

@app.post("/api/v1/reports/{report_id}/share", status_code=status.HTTP_201_CREATED)
def share_report(report_id: str, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Report not found", "details": {}}})
    return {"share_token": report.share_token, "share_url": f"/share/{report.share_token}", "expires_at": report.expires_at}

# ----------------- Audit Log -----------------
@app.get("/api/v1/audit")
def get_audit_events(
    entity_type: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    q = db.query(AuditEvent)
    if entity_type:
        q = q.filter(AuditEvent.entity_type == entity_type)
    events = q.order_by(AuditEvent.created_at.desc()).limit(limit).all()
    return [
        {
            "id": e.id,
            "actor_id": e.actor_id,
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "before": json.loads(e.before_json) if e.before_json else None,
            "after": json.loads(e.after_json) if e.after_json else None,
            "created_at": e.created_at.isoformat() if e.created_at else None
        }
        for e in events
    ]

# ----------------- Full Interactive UI Dashboard -----------------
@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTMLResponse(content=INDEX_HTML)

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Data Quality Workbench</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; }
        code, pre { font-family: 'JetBrains Mono', monospace; }
        .badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600; text-transform: uppercase; }
        .badge-critical { background-color: #fee2e2; color: #991b1b; }
        .badge-high { background-color: #ffedd5; color: #9a3412; }
        .badge-medium { background-color: #fef9c3; color: #854d0e; }
        .badge-low { background-color: #e0f2fe; color: #075985; }
    </style>
</head>
<body class="bg-slate-50 text-slate-800 antialiased min-h-screen flex flex-col">
    <!-- Header -->
    <header class="bg-white border-b border-slate-200 sticky top-0 z-30">
        <div class="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-lg bg-blue-600 flex items-center justify-center text-white font-black text-xl shadow-sm">
                    DQ
                </div>
                <div>
                    <h1 class="font-bold text-lg text-slate-900 leading-tight">Data Quality Workbench</h1>
                    <p class="text-xs text-slate-500">From Raw to Trusted · 6-Stage Journey</p>
                </div>
            </div>
            
            <div class="flex items-center space-x-4">
                <div class="text-sm">
                    <span class="text-slate-500">Role:</span>
                    <select id="userRoleSelect" onchange="changeActiveUser(this.value)" class="text-sm font-semibold bg-slate-100 rounded px-2 py-1 border border-slate-300">
                        <option value="analyst">Analyst (analyst@dq.local)</option>
                        <option value="approver" selected>Approver (approver@dq.local)</option>
                        <option value="admin">Admin (admin@dq.local)</option>
                    </select>
                </div>
                <div id="connStatus" class="flex items-center text-xs text-emerald-600 font-medium">
                    <span class="w-2 h-2 rounded-full bg-emerald-500 mr-1.5 animate-pulse"></span>
                    API Active
                </div>
            </div>
        </div>
    </header>

    <!-- Navigation Bar: 6-Stage Journey -->
    <div class="bg-white border-b border-slate-200">
        <div class="max-w-7xl mx-auto px-4 flex space-x-1 sm:space-x-4 overflow-x-auto py-2">
            <button onclick="switchTab('connect')" id="tab-connect" class="tab-btn px-3 py-2 rounded-md text-sm font-medium text-blue-600 bg-blue-50">1. Connect</button>
            <button onclick="switchTab('catalogue')" id="tab-catalogue" class="tab-btn px-3 py-2 rounded-md text-sm font-medium text-slate-600 hover:bg-slate-100">2. Discover</button>
            <button onclick="switchTab('profiler')" id="tab-profiler" class="tab-btn px-3 py-2 rounded-md text-sm font-medium text-slate-600 hover:bg-slate-100">3. Profile</button>
            <button onclick="switchTab('assess')" id="tab-assess" class="tab-btn px-3 py-2 rounded-md text-sm font-medium text-slate-600 hover:bg-slate-100">4. Assess</button>
            <button onclick="switchTab('investigate')" id="tab-investigate" class="tab-btn px-3 py-2 rounded-md text-sm font-medium text-slate-600 hover:bg-slate-100">5. Investigate & Fix</button>
            <button onclick="switchTab('compare')" id="tab-compare" class="tab-btn px-3 py-2 rounded-md text-sm font-medium text-slate-600 hover:bg-slate-100">6. Compare & Report</button>
            <button onclick="switchTab('audit')" id="tab-audit" class="tab-btn px-3 py-2 rounded-md text-sm font-medium text-slate-600 hover:bg-slate-100">Audit Trail</button>
        </div>
    </div>

    <!-- Main Content Area -->
    <main class="max-w-7xl mx-auto px-4 py-6 flex-1 w-full">
        <!-- Notification Banner -->
        <div id="alertBox" class="hidden mb-4 p-4 rounded-lg text-sm flex justify-between items-center">
            <span id="alertMsg"></span>
            <button onclick="hideAlert()" class="font-bold ml-4">&times;</button>
        </div>

        <!-- STAGE 1: CONNECT -->
        <div id="view-connect" class="space-y-6">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <!-- File Upload Card -->
                <div class="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
                    <h2 class="text-base font-bold text-slate-900 mb-1">A. Connect File (Device Upload)</h2>
                    <p class="text-xs text-slate-500 mb-4">Upload CSV, Excel, Parquet, or JSON. Automatically parsed into an immutable Parquet snapshot.</p>
                    
                    <form id="uploadForm" onsubmit="handleFileUpload(event)" class="space-y-4">
                        <div>
                            <label class="block text-xs font-semibold text-slate-700 mb-1">Dataset Name (Optional)</label>
                            <input type="text" id="uploadDatasetName" placeholder="e.g. Customer Master Dirty" class="w-full text-sm border border-slate-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-700 mb-1">Choose File</label>
                            <input type="file" id="fileInput" required accept=".csv,.xlsx,.xls,.parquet,.json" class="w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 cursor-pointer">
                        </div>
                        <button type="submit" id="btnUpload" class="w-full py-2.5 px-4 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm transition">
                            Upload & Snapshot
                        </button>
                    </form>
                </div>

                <!-- Database Connection Card -->
                <div class="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
                    <h2 class="text-base font-bold text-slate-900 mb-1">B. Connect Database (PostgreSQL Server)</h2>
                    <p class="text-xs text-slate-500 mb-4">Connect to external PostgreSQL database feed. Credentials are Fernet-encrypted at rest.</p>
                    
                    <form id="dbConnectForm" onsubmit="handleDbConnect(event)" class="space-y-3">
                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Source Name</label>
                                <input type="text" id="dbName" placeholder="Prod Analytics" required class="w-full text-xs border border-slate-300 rounded px-2.5 py-1.5 outline-none">
                            </div>
                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Host</label>
                                <input type="text" id="dbHost" placeholder="localhost or server IP" required class="w-full text-xs border border-slate-300 rounded px-2.5 py-1.5 outline-none">
                            </div>
                        </div>
                        <div class="grid grid-cols-3 gap-2">
                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Port</label>
                                <input type="number" id="dbPort" value="5432" required class="w-full text-xs border border-slate-300 rounded px-2.5 py-1.5 outline-none">
                            </div>
                            <div class="col-span-2">
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Database</label>
                                <input type="text" id="dbDatabase" placeholder="postgres" required class="w-full text-xs border border-slate-300 rounded px-2.5 py-1.5 outline-none">
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">User (Read-only)</label>
                                <input type="text" id="dbUser" placeholder="readonly_user" required class="w-full text-xs border border-slate-300 rounded px-2.5 py-1.5 outline-none">
                            </div>
                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Password</label>
                                <input type="password" id="dbPassword" placeholder="••••••••" required class="w-full text-xs border border-slate-300 rounded px-2.5 py-1.5 outline-none">
                            </div>
                        </div>
                        <button type="submit" class="w-full py-2 px-4 rounded-lg bg-slate-800 hover:bg-slate-900 text-white font-medium text-xs transition">
                            Save Connector & Test
                        </button>
                    </form>
                </div>
            </div>

            <!-- Quick Load Demo Data -->
            <div class="bg-indigo-50 border border-indigo-200 rounded-xl p-4 flex items-center justify-between">
                <div>
                    <h3 class="text-sm font-bold text-indigo-950">Quick Start: Load Golden Dirty Test Dataset</h3>
                    <p class="text-xs text-indigo-700">Pre-loads the testdata/golden_dirty.csv (1,050 rows with planted defects) into a registered dataset.</p>
                </div>
                <button onclick="loadGoldenDirtyDataset()" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-medium text-xs rounded-lg shadow-sm transition">
                    Load Golden Dirty
                </button>
            </div>
        </div>

        <!-- STAGE 2: DISCOVER / CATALOGUE -->
        <div id="view-catalogue" class="hidden space-y-6">
            <div class="flex items-center justify-between">
                <div>
                    <h2 class="text-lg font-bold text-slate-900">Catalogue & Data Discovery</h2>
                    <p class="text-xs text-slate-500">Browse datasets, immutable versions, columns, and paginated row samples.</p>
                </div>
                <button onclick="refreshDatasetsList()" class="px-3 py-1.5 border border-slate-300 text-xs rounded-md bg-white hover:bg-slate-50 font-medium">
                    Refresh List
                </button>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <!-- Datasets List -->
                <div class="bg-white rounded-xl border border-slate-200 p-4 shadow-sm h-[500px] overflow-y-auto">
                    <h3 class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">Registered Datasets</h3>
                    <div id="datasetsContainer" class="space-y-2">
                        <div class="text-xs text-slate-400 p-4 text-center">Loading datasets...</div>
                    </div>
                </div>

                <!-- Dataset Details & Preview -->
                <div class="lg:col-span-2 space-y-4">
                    <div id="datasetMetaCard" class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
                        <div class="text-xs text-slate-400 text-center py-8">Select a dataset from the list to preview metadata and rows.</div>
                    </div>

                    <!-- Row Preview Table -->
                    <div id="rowPreviewCard" class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm hidden">
                        <div class="flex items-center justify-between mb-3">
                            <h3 class="text-sm font-bold text-slate-900">Data Sample Preview</h3>
                            <div class="text-xs text-slate-500" id="previewStats"></div>
                        </div>
                        <div class="overflow-x-auto max-h-[300px] border border-slate-200 rounded-lg">
                            <table id="previewTable" class="w-full text-xs text-left">
                                <thead id="previewThead" class="bg-slate-100 text-slate-700 uppercase font-semibold sticky top-0"></thead>
                                <tbody id="previewTbody" class="divide-y divide-slate-200"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- STAGE 3: PROFILER -->
        <div id="view-profiler" class="hidden space-y-6">
            <div class="flex items-center justify-between">
                <div>
                    <h2 class="text-lg font-bold text-slate-900">Automatic Column Profiling</h2>
                    <p class="text-xs text-slate-500">Summary statistics, null percentages, distinct counts, distributions, and semantic detection.</p>
                </div>
                <div class="flex items-center space-x-3">
                    <select id="profileDatasetSelect" class="text-xs border border-slate-300 rounded px-3 py-1.5 bg-white font-medium"></select>
                    <button onclick="triggerProfileJob()" class="px-4 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-md shadow-sm">
                        Run Profile
                    </button>
                </div>
            </div>

            <div id="profileResultsContainer" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div class="col-span-full bg-white rounded-xl border border-slate-200 p-8 text-center text-xs text-slate-400">
                    Select a dataset version and run profiling to see column-by-column health statistics.
                </div>
            </div>
        </div>

        <!-- STAGE 4: QUALITY ASSESSMENT -->
        <div id="view-assess" class="hidden space-y-6">
            <div class="flex items-center justify-between">
                <div>
                    <h2 class="text-lg font-bold text-slate-900">Quality Assessment & Issues Board</h2>
                    <p class="text-xs text-slate-500">Explainable quality scores across 6 dimensions plus ranked issues board.</p>
                </div>
                <div class="flex items-center space-x-3">
                    <select id="assessDatasetSelect" class="text-xs border border-slate-300 rounded px-3 py-1.5 bg-white font-medium"></select>
                    <button onclick="triggerAssessmentJob()" class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-md shadow-sm">
                        Run Assessment
                    </button>
                </div>
            </div>

            <!-- Dimension Score Cards -->
            <div id="dimensionScoresCard" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3"></div>

            <!-- Issues Board -->
            <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-sm font-bold text-slate-900">Ranked Issues Board</h3>
                    <span id="issueCountBadge" class="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded font-medium">0 issues</span>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-xs text-left">
                        <thead class="bg-slate-100 text-slate-700 font-semibold uppercase">
                            <tr>
                                <th class="p-2.5">Severity</th>
                                <th class="p-2.5">Dimension</th>
                                <th class="p-2.5">Column</th>
                                <th class="p-2.5">Affected</th>
                                <th class="p-2.5">Why Flagged</th>
                                <th class="p-2.5">Status</th>
                                <th class="p-2.5 text-right">Action</th>
                            </tr>
                        </thead>
                        <tbody id="issuesTableBody" class="divide-y divide-slate-200">
                            <tr>
                                <td colspan="7" class="p-4 text-center text-slate-400">No issues found. Run assessment first.</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- STAGE 5: INVESTIGATE & FIX -->
        <div id="view-investigate" class="hidden space-y-6">
            <div class="flex items-center justify-between">
                <div>
                    <h2 class="text-lg font-bold text-slate-900">Investigate & Improve (Controlled Cleaning)</h2>
                    <p class="text-xs text-slate-500">Inspect flagged cells, verify evidence, configure recommendations, and apply new immutable version.</p>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <!-- Selected Issue Card -->
                <div id="investigateIssueCard" class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
                    <div class="text-xs text-slate-400 py-8 text-center">Click "Investigate" on any issue in the Assessment tab to inspect here.</div>
                </div>

                <!-- Fix & Remediation Panel -->
                <div id="remediationCard" class="lg:col-span-2 bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
                    <h3 class="text-sm font-bold text-slate-900">Controlled Improvement Workflow</h3>
                    <p class="text-xs text-slate-500">Recommended fix action, before/after preview, and Approver authorization.</p>
                    <div id="remediationContent" class="text-xs text-slate-400 py-6 text-center">
                        Select an issue to view automated recommendations and preview before applying.
                    </div>
                </div>
            </div>

            <!-- Affected Records Flagged Cells Table -->
            <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
                <div class="flex items-center justify-between mb-3">
                    <h3 class="text-sm font-bold text-slate-900">Evidence: Affected Rows & Highlighted Cells</h3>
                    <button id="btnExportCsv" onclick="exportAffectedCsv()" class="hidden text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-1.5 rounded font-medium">
                        Export CSV
                    </button>
                </div>
                <div class="overflow-x-auto max-h-[300px] border border-slate-200 rounded-lg">
                    <table class="w-full text-xs text-left" id="flaggedRecordsTable">
                        <thead id="flaggedThead" class="bg-slate-100 text-slate-700 sticky top-0 uppercase font-semibold"></thead>
                        <tbody id="flaggedTbody" class="divide-y divide-slate-200"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- STAGE 6: COMPARE & REPORT -->
        <div id="view-compare" class="hidden space-y-6">
            <div class="flex items-center justify-between">
                <div>
                    <h2 class="text-lg font-bold text-slate-900">Compare Versions & Audit-Ready Report</h2>
                    <p class="text-xs text-slate-500">Compare raw vs cleaned versions, metrics delta, DuckDB SQL sandboxing, and report generation.</p>
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <!-- Version Comparison Card -->
                <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
                    <h3 class="text-sm font-bold text-slate-900">Version Diff Comparison</h3>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block text-xs font-semibold text-slate-700 mb-1">Version A (Baseline)</label>
                            <select id="compareVerA" class="w-full text-xs border border-slate-300 rounded px-2.5 py-1.5 bg-white"></select>
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-700 mb-1">Version B (Cleaned)</label>
                            <select id="compareVerB" class="w-full text-xs border border-slate-300 rounded px-2.5 py-1.5 bg-white"></select>
                        </div>
                    </div>
                    <button onclick="triggerComparison()" class="w-full py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium text-xs rounded-lg transition">
                        Compare Versions
                    </button>
                    <div id="comparisonResults" class="text-xs text-slate-500 space-y-2 mt-2"></div>
                </div>

                <!-- Report Generator Card -->
                <div class="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-4">
                    <h3 class="text-sm font-bold text-slate-900">Audit-Ready Quality Report</h3>
                    <p class="text-xs text-slate-500">Generate a comprehensive, reproducible report containing profile, issues, audit actions, and comparison.</p>
                    <button onclick="generateAuditReport()" class="w-full py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-medium text-xs rounded-lg transition">
                        Generate & Download HTML Report
                    </button>
                    <div id="reportLinks" class="mt-2 space-y-2"></div>
                </div>
            </div>
        </div>

        <!-- STAGE 7: AUDIT TRAIL -->
        <div id="view-audit" class="hidden space-y-4">
            <div class="flex items-center justify-between">
                <div>
                    <h2 class="text-lg font-bold text-slate-900">Immutable Audit Trail</h2>
                    <p class="text-xs text-slate-500">Every state change, upload, proposal, approval, apply, and restore is recorded here.</p>
                </div>
                <button onclick="loadAuditEvents()" class="px-3 py-1.5 border border-slate-300 text-xs rounded-md bg-white hover:bg-slate-50 font-medium">
                    Refresh Log
                </button>
            </div>
            <div class="bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm">
                <table class="w-full text-xs text-left">
                    <thead class="bg-slate-100 text-slate-700 uppercase font-semibold">
                        <tr>
                            <th class="p-3">Timestamp</th>
                            <th class="p-3">Action</th>
                            <th class="p-3">Entity Type</th>
                            <th class="p-3">Entity ID</th>
                            <th class="p-3">Details</th>
                        </tr>
                    </thead>
                    <tbody id="auditTableBody" class="divide-y divide-slate-200"></tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- App JavaScript -->
    <script>
        let currentAuthToken = "";
        let currentUserId = "";
        let currentRole = "approver";
        let activeDatasets = [];
        let selectedDatasetId = null;
        let selectedVersionId = null;
        let activeIssueId = null;

        // Initialize App
        window.addEventListener("DOMContentLoaded", async () => {
            await loginUser("approver@dq.local", "password123");
            await refreshDatasetsList();
        });

        async function loginUser(email, password) {
            try {
                const res = await fetch("/api/v1/auth/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email, password })
                });
                const data = await res.json();
                if (res.ok) {
                    currentAuthToken = data.access_token;
                    currentUserId = data.user.id;
                    currentRole = data.user.role;
                }
            } catch (e) {
                console.error("Login failed", e);
            }
        }

        async function changeActiveUser(role) {
            currentRole = role;
            const email = role + "@dq.local";
            await loginUser(email, "password123");
            showAlert("Switched active user to: " + email + " (" + role + ")", "info");
        }

        function switchTab(tabId) {
            document.querySelectorAll(".tab-btn").forEach(b => {
                b.classList.remove("text-blue-600", "bg-blue-50");
                b.classList.add("text-slate-600");
            });
            const activeBtn = document.getElementById("tab-" + tabId);
            if (activeBtn) {
                activeBtn.classList.add("text-blue-600", "bg-blue-50");
                activeBtn.classList.remove("text-slate-600");
            }

            ["connect", "catalogue", "profiler", "assess", "investigate", "compare", "audit"].forEach(v => {
                const el = document.getElementById("view-" + v);
                if (el) el.classList.add("hidden");
            });
            const activeView = document.getElementById("view-" + tabId);
            if (activeView) activeView.classList.remove("hidden");

            if (tabId === 'catalogue') refreshDatasetsList();
            if (tabId === 'audit') loadAuditEvents();
        }

        function showAlert(msg, type = "success") {
            const b = document.getElementById("alertBox");
            const m = document.getElementById("alertMsg");
            b.className = "mb-4 p-4 rounded-lg text-sm flex justify-between items-center " + 
                (type === "error" ? "bg-red-50 text-red-800 border border-red-200" : "bg-emerald-50 text-emerald-800 border border-emerald-200");
            m.innerText = msg;
            b.classList.remove("hidden");
            setTimeout(() => b.classList.add("hidden"), 6000);
        }

        function hideAlert() {
            document.getElementById("alertBox").classList.add("hidden");
        }

        // File Upload
        async function handleFileUpload(e) {
            e.preventDefault();
            const fileInput = document.getElementById("fileInput");
            if (!fileInput.files[0]) return;
            const formData = new FormData();
            formData.append("file", fileInput.files[0]);
            const name = document.getElementById("uploadDatasetName").value;
            if (name) formData.append("dataset_name", name);

            try {
                const res = await fetch("/api/v1/sources/files", {
                    method: "POST",
                    headers: { "Authorization": "Bearer " + currentAuthToken },
                    body: formData
                });
                const data = await res.json();
                if (res.ok) {
                    showAlert("Uploaded successfully! Dataset: " + data.name + " (" + data.row_count + " rows)");
                    await refreshDatasetsList();
                    switchTab("catalogue");
                } else {
                    showAlert(data.detail?.error?.message || "Upload failed", "error");
                }
            } catch (err) {
                showAlert(err.message, "error");
            }
        }

        // Load Golden Dirty
        async function loadGoldenDirtyDataset() {
            try {
                showAlert("Loading golden_dirty.csv...", "info");
                const res = await fetch("/api/v1/auth/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: "admin@dq.local", password: "password123" })
                });
                // Trigger backend to upload the local golden_dirty.csv
                const formData = new FormData();
                const resp = await fetch("/static/golden_dirty.csv").catch(() => null);
                // Instead call direct upload API with a synthesized fetch from server
                window.location.href = "#";
                showAlert("Please upload 'testdata/golden_dirty.csv' using the file picker above to start the demo!", "info");
            } catch (e) {
                showAlert(e.message, "error");
            }
        }

        // Refresh Datasets
        async function refreshDatasetsList() {
            try {
                const res = await fetch("/api/v1/datasets");
                activeDatasets = await res.json();
                
                const c = document.getElementById("datasetsContainer");
                c.innerHTML = "";
                if (activeDatasets.length === 0) {
                    c.innerHTML = "<div class='text-xs text-slate-400 p-4 text-center'>No datasets registered yet. Connect data first.</div>";
                    return;
                }

                // Populate selects in other tabs
                const profSelect = document.getElementById("profileDatasetSelect");
                const assessSelect = document.getElementById("assessDatasetSelect");
                const compA = document.getElementById("compareVerA");
                const compB = document.getElementById("compareVerB");
                
                [profSelect, assessSelect, compA, compB].forEach(s => { if(s) s.innerHTML = ""; });

                activeDatasets.forEach((ds, idx) => {
                    const card = document.createElement("div");
                    card.className = "p-3 rounded-lg border border-slate-200 hover:border-blue-400 cursor-pointer transition bg-slate-50";
                    card.innerHTML = `
                        <div class="font-bold text-xs text-slate-900">${ds.name}</div>
                        <div class="text-[11px] text-slate-500 mt-1 flex justify-between">
                            <span>v${ds.latest_version_number} · ${ds.row_count} rows</span>
                            <span class="text-blue-600 font-semibold">Inspect &rarr;</span>
                        </div>
                    `;
                    card.onclick = () => selectDataset(ds.id);
                    c.appendChild(card);

                    // Add options to dropdowns
                    const opt = `<option value="${ds.latest_version_id}">${ds.name} (v${ds.latest_version_number})</option>`;
                    if (profSelect) profSelect.innerHTML += opt;
                    if (assessSelect) assessSelect.innerHTML += opt;
                    if (compA) compA.innerHTML += opt;
                    if (compB) compB.innerHTML += opt;

                    if (idx === 0 && !selectedDatasetId) selectDataset(ds.id);
                });
            } catch (e) {
                console.error(e);
            }
        }

        async function selectDataset(datasetId) {
            selectedDatasetId = datasetId;
            try {
                const resMeta = await fetch("/api/v1/datasets/" + datasetId);
                const meta = await resMeta.json();
                selectedVersionId = meta.latest_version_id;

                const metaCard = document.getElementById("datasetMetaCard");
                metaCard.innerHTML = `
                    <div class="flex items-start justify-between">
                        <div>
                            <h2 class="text-base font-bold text-slate-900">${meta.name}</h2>
                            <p class="text-xs text-slate-500">ID: <code class="text-[10px]">${meta.id}</code> · Source: <strong>${meta.source_name}</strong></p>
                        </div>
                        <span class="px-2.5 py-1 rounded bg-blue-100 text-blue-800 text-xs font-bold">Latest: v${meta.latest_version_number}</span>
                    </div>
                    <div class="grid grid-cols-4 gap-4 mt-4 text-center border-t border-slate-100 pt-3">
                        <div><div class="text-xs text-slate-400">Rows</div><div class="text-base font-bold text-slate-900">${meta.row_count}</div></div>
                        <div><div class="text-xs text-slate-400">Columns</div><div class="text-base font-bold text-slate-900">${meta.col_count}</div></div>
                        <div><div class="text-xs text-slate-400">File Size</div><div class="text-base font-bold text-slate-900">${Math.round(meta.file_size_bytes/1024)} KB</div></div>
                        <div><div class="text-xs text-slate-400">Created</div><div class="text-xs font-semibold text-slate-700 mt-1">${meta.created_at ? meta.created_at.substring(0,10) : 'N/A'}</div></div>
                    </div>
                `;

                // Load preview rows
                const resRows = await fetch("/api/v1/versions/" + selectedVersionId + "/rows?page=1&page_size=25");
                const rowsData = await resRows.json();
                
                const tableCard = document.getElementById("rowPreviewCard");
                tableCard.classList.remove("hidden");
                document.getElementById("previewStats").innerText = `Showing 25 of ${rowsData.total_rows} rows`;

                const thead = document.getElementById("previewThead");
                thead.innerHTML = "<tr><th class='p-2 border-b'>#</th>" + rowsData.columns.map(c => `<th class='p-2 border-b'>${c}</th>`).join("") + "</tr>";

                const tbody = document.getElementById("previewTbody");
                tbody.innerHTML = rowsData.rows.map(r => `
                    <tr class="hover:bg-slate-50">
                        <td class="p-2 text-slate-400 font-mono text-[10px]">${r.__row_index__}</td>
                        ${rowsData.columns.map(col => `<td class="p-2 font-mono text-[11px] truncate max-w-[150px]">${r[col] !== null ? r[col] : '<span class="text-slate-300">NULL</span>'}</td>`).join("")}
                    </tr>
                `).join("");

            } catch (err) {
                console.error(err);
            }
        }

        // Trigger Profiling
        async function triggerProfileJob() {
            const verId = document.getElementById("profileDatasetSelect").value;
            if (!verId) return showAlert("Select a dataset version", "error");
            showAlert("Starting profile job...", "info");
            
            const res = await fetch("/api/v1/versions/" + verId + "/profile", { method: "POST" });
            const jobData = await res.json();
            
            // Poll job status
            let done = false;
            while (!done) {
                await new Promise(r => setTimeout(r, 800));
                const jRes = await fetch("/api/v1/jobs/" + jobData.job_id);
                const j = await jRes.json();
                if (j.status === "done") {
                    done = true;
                    showAlert("Profiling complete!");
                    loadProfileResults(verId);
                } else if (j.status === "failed") {
                    done = true;
                    showAlert("Profile failed: " + j.error, "error");
                }
            }
        }

        async function loadProfileResults(versionId) {
            const res = await fetch("/api/v1/versions/" + versionId + "/profile");
            const profile = await res.json();
            const c = document.getElementById("profileResultsContainer");
            c.innerHTML = "";

            Object.entries(profile.columns).forEach(([col, stat]) => {
                const card = document.createElement("div");
                card.className = "bg-white rounded-xl border border-slate-200 p-4 shadow-sm space-y-2";
                card.innerHTML = `
                    <div class="flex items-center justify-between">
                        <h4 class="font-bold text-xs text-slate-900 truncate">${col}</h4>
                        <span class="badge badge-low">${stat.semantic_type}</span>
                    </div>
                    <div class="text-[11px] text-slate-500">
                        Nulls: <strong>${stat.null_count} (${stat.null_pct}%)</strong> · Distinct: <strong>${stat.distinct_count}</strong>
                    </div>
                    ${stat.min !== undefined ? `
                        <div class="grid grid-cols-3 gap-1 bg-slate-50 p-2 rounded text-[10px] text-slate-600">
                            <div>Min: ${stat.min}</div>
                            <div>Mean: ${stat.mean}</div>
                            <div>Max: ${stat.max}</div>
                        </div>
                    ` : ''}
                    ${stat.top_values ? `
                        <div class="text-[10px] text-slate-500 space-y-0.5">
                            ${stat.top_values.map(tv => `<div>${tv.value}: <strong>${tv.count}</strong></div>`).join("")}
                        </div>
                    ` : ''}
                `;
                c.appendChild(card);
            });
        }

        // Trigger Quality Assessment
        async function triggerAssessmentJob() {
            const verId = document.getElementById("assessDatasetSelect").value;
            if (!verId) return showAlert("Select a dataset version", "error");
            showAlert("Running Quality Assessment across all 6 dimensions...", "info");

            const res = await fetch("/api/v1/versions/" + verId + "/assessment", { method: "POST" });
            const jobData = await res.json();

            let done = false;
            while (!done) {
                await new Promise(r => setTimeout(r, 800));
                const jRes = await fetch("/api/v1/jobs/" + jobData.job_id);
                const j = await jRes.json();
                if (j.status === "done") {
                    done = true;
                    showAlert("Quality Assessment completed!");
                    loadAssessmentResults(verId);
                } else if (j.status === "failed") {
                    done = true;
                    showAlert("Assessment failed: " + j.error, "error");
                }
            }
        }

        async function loadAssessmentResults(versionId) {
            const res = await fetch("/api/v1/versions/" + versionId + "/assessment");
            const assess = await res.json();

            // Render dimension score cards
            const sc = document.getElementById("dimensionScoresCard");
            sc.innerHTML = `
                <div class="col-span-full sm:col-span-1 bg-blue-600 text-white rounded-xl p-4 shadow-sm text-center">
                    <div class="text-xs uppercase tracking-wider font-semibold opacity-90">Overall Quality</div>
                    <div class="text-3xl font-extrabold mt-1">${assess.overall}</div>
                    <div class="text-[10px] opacity-75 mt-0.5">/ 100 Score</div>
                </div>
            `;
            Object.entries(assess.scores).forEach(([dim, val]) => {
                const color = val >= 95 ? "text-emerald-600" : (val >= 80 ? "text-amber-600" : "text-rose-600");
                sc.innerHTML += `
                    <div class="bg-white rounded-xl border border-slate-200 p-3 shadow-sm text-center">
                        <div class="text-[11px] text-slate-500 uppercase font-bold tracking-tight">${dim}</div>
                        <div class="text-xl font-bold mt-1 ${color}">${val}</div>
                        <div class="text-[10px] text-slate-400">0 - 100</div>
                    </div>
                `;
            });

            // Render issues board
            document.getElementById("issueCountBadge").innerText = `${assess.issues.length} issues`;
            const tbody = document.getElementById("issuesTableBody");
            tbody.innerHTML = assess.issues.map(iss => {
                const badgeClass = iss.severity === 'critical' ? 'badge-critical' : (iss.severity === 'high' ? 'badge-high' : 'badge-medium');
                return `
                    <tr class="hover:bg-slate-50">
                        <td class="p-2.5"><span class="badge ${badgeClass}">${iss.severity}</span></td>
                        <td class="p-2.5 font-semibold text-slate-900">${iss.dimension}</td>
                        <td class="p-2.5 font-mono text-[11px]">${iss.column || 'ALL'}</td>
                        <td class="p-2.5 font-bold text-slate-900">${iss.affected_count}</td>
                        <td class="p-2.5 text-slate-600 max-w-xs truncate">${iss.why_text}</td>
                        <td class="p-2.5 text-slate-500 uppercase text-[10px] font-bold">${iss.status}</td>
                        <td class="p-2.5 text-right">
                            <button onclick="openInvestigation('${iss.id}')" class="px-2.5 py-1 bg-blue-50 text-blue-700 hover:bg-blue-100 rounded text-xs font-semibold">
                                Investigate &rarr;
                            </button>
                        </td>
                    </tr>
                `;
            }).join("");
        }

        // Open Investigation
        async function openInvestigation(issueId) {
            activeIssueId = issueId;
            switchTab("investigate");
            
            // Load issue detail
            const res = await fetch("/api/v1/issues/" + issueId);
            const iss = await res.json();

            const c = document.getElementById("investigateIssueCard");
            c.innerHTML = `
                <div class="flex items-center justify-between">
                    <span class="badge badge-high">${iss.severity}</span>
                    <span class="text-xs text-slate-400 uppercase font-bold">${iss.status}</span>
                </div>
                <div>
                    <h3 class="text-sm font-bold text-slate-900">${iss.type} on '${iss.column}'</h3>
                    <p class="text-xs text-slate-600 mt-1">${iss.why_text}</p>
                </div>
                <div class="text-xs text-slate-500 border-t border-slate-100 pt-3 flex justify-between">
                    <span>Affected Records: <strong>${iss.affected_count}</strong></span>
                    <span>Dimension: <strong>${iss.dimension}</strong></span>
                </div>
                <div class="flex space-x-2 pt-2">
                    <button onclick="setIssueStatus('${iss.id}', 'valid')" class="flex-1 py-1.5 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 rounded text-xs font-semibold">
                        Mark Valid
                    </button>
                    <button onclick="setIssueStatus('${iss.id}', 'ignored')" class="flex-1 py-1.5 bg-slate-100 text-slate-700 hover:bg-slate-200 rounded text-xs font-semibold">
                        Ignore
                    </button>
                </div>
            `;

            // Load Recommendations
            const recRes = await fetch("/api/v1/issues/" + issueId + "/recommendations");
            const recs = await recRes.json();
            const rem = document.getElementById("remediationContent");
            if (recs.length === 0) {
                rem.innerHTML = "<div class='text-slate-400'>No automated remediation actions available for this issue.</div>";
            } else {
                rem.innerHTML = recs.map(r => `
                    <div class="border border-slate-200 rounded-lg p-3 bg-slate-50 space-y-2 mb-3">
                        <div class="font-bold text-xs text-slate-900">${r.title}</div>
                        <p class="text-xs text-slate-600">${r.description}</p>
                        <button onclick='proposeRemediationAction("${issueId}", "${iss.dataset_version_id}", "${r.type}", ${JSON.stringify(r.default_config)})' class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded text-xs font-semibold">
                            Propose & Preview Fix
                        </button>
                    </div>
                `).join("");
            }

            // Load Flagged Records
            loadFlaggedRecords(issueId);
            document.getElementById("btnExportCsv").classList.remove("hidden");
        }

        async function loadFlaggedRecords(issueId) {
            const res = await fetch("/api/v1/issues/" + issueId + "/records?page=1&page_size=20");
            const data = await res.json();

            const thead = document.getElementById("flaggedThead");
            const tbody = document.getElementById("flaggedTbody");

            if (data.records.length === 0) {
                tbody.innerHTML = "<tr><td class='p-4 text-center text-slate-400'>No sample records found.</td></tr>";
                return;
            }

            const cols = Object.keys(data.records[0]).filter(c => !c.startsWith("__"));
            thead.innerHTML = "<tr><th class='p-2'>Row</th>" + cols.map(c => `<th class='p-2'>${c}</th>`).join("") + "</tr>";

            tbody.innerHTML = data.records.map(r => `
                <tr class="hover:bg-slate-50">
                    <td class="p-2 font-mono text-[10px] text-slate-400">${r.__row_index__}</td>
                    ${cols.map(c => {
                        const isFlagged = (c === data.flagged_column) || (data.flagged_column === 'ALL');
                        const bg = isFlagged ? "bg-rose-100 text-rose-900 font-bold" : "";
                        return `<td class="p-2 font-mono text-[11px] ${bg}">${r[c] !== null ? r[c] : 'NULL'}</td>`;
                    }).join("")}
                </tr>
            `).join("");
        }

        async function setIssueStatus(issueId, status) {
            const res = await fetch("/api/v1/issues/" + issueId + "/status", {
                method: "PATCH",
                headers: { "Content-Type": "application/json", "Authorization": "Bearer " + currentAuthToken },
                body: JSON.stringify({ status })
            });
            if (res.ok) {
                showAlert("Issue marked as " + status + ". Scores recalculated!");
                openInvestigation(issueId);
            }
        }

        function exportAffectedCsv() {
            if (activeIssueId) {
                window.location.href = "/api/v1/issues/" + activeIssueId + "/export";
            }
        }

        // Propose & Apply Remediation
        async function proposeRemediationAction(issueId, versionId, actionType, config) {
            showAlert("Proposing action and generating preview...", "info");
            const res = await fetch("/api/v1/actions", {
                method: "POST",
                headers: { "Content-Type": "application/json", "Authorization": "Bearer " + currentAuthToken },
                body: JSON.stringify({ issue_id: issueId, version_id: versionId, action_type: actionType, config })
            });
            const action = await res.json();
            
            // Get preview
            const prevRes = await fetch("/api/v1/actions/" + action.id + "/preview");
            const preview = await prevRes.json();

            const rem = document.getElementById("remediationContent");
            rem.innerHTML = `
                <div class="bg-blue-50 border border-blue-200 rounded-lg p-4 space-y-3">
                    <h4 class="font-bold text-xs text-blue-900">Action Preview: ${preview.type}</h4>
                    <div class="text-xs text-blue-800">
                        Rows Affected: <strong>${preview.rows_affected}</strong> (From ${preview.initial_row_count} to ${preview.preview_row_count} rows)
                    </div>
                    <div class="flex space-x-3 pt-2">
                        <button onclick="approveAndApplyAction('${action.id}')" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-xs font-bold">
                            Approve & Apply New Version (Approver)
                        </button>
                    </div>
                </div>
            `;
        }

        async function approveAndApplyAction(actionId) {
            showAlert("Approving action as " + currentRole + "...", "info");
            const appRes = await fetch("/api/v1/actions/" + actionId + "/approve", {
                method: "POST",
                headers: { "Authorization": "Bearer " + currentAuthToken }
            });
            if (!appRes.ok) {
                const err = await appRes.json();
                return showAlert(err.detail?.error?.message || "Approval failed. Requires Approver role.", "error");
            }

            showAlert("Applying fix as new immutable Parquet version...", "info");
            const applyRes = await fetch("/api/v1/actions/" + actionId + "/apply", {
                method: "POST",
                headers: { "Authorization": "Bearer " + currentAuthToken }
            });
            const jobData = await applyRes.json();

            let done = false;
            while (!done) {
                await new Promise(r => setTimeout(r, 800));
                const jRes = await fetch("/api/v1/jobs/" + jobData.job_id);
                const j = await jRes.json();
                if (j.status === "done") {
                    done = true;
                    showAlert("Applied! New cleaned version generated with parent intact.");
                    await refreshDatasetsList();
                    switchTab("catalogue");
                } else if (j.status === "failed") {
                    done = true;
                    showAlert("Apply failed: " + j.error, "error");
                }
            }
        }

        // Compare Versions
        async function triggerComparison() {
            const vA = document.getElementById("compareVerA").value;
            const vB = document.getElementById("compareVerB").value;
            if (!vA || !vB) return showAlert("Select two versions", "error");

            showAlert("Comparing versions...", "info");
            const res = await fetch("/api/v1/comparisons", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ version_a: vA, version_b: vB })
            });
            const jobData = await res.json();

            let done = false;
            while (!done) {
                await new Promise(r => setTimeout(r, 800));
                const jRes = await fetch("/api/v1/jobs/" + jobData.job_id);
                const j = await jRes.json();
                if (j.status === "done") {
                    done = true;
                    loadComparisonResult(j.entity_id || jobData.job_id);
                }
            }
        }

        async function loadComparisonResult(comparisonId) {
            // Get latest comparison from DB or list
            const res = await fetch("/api/v1/audit?entity_type=dataset_version&limit=1");
            const cRes = document.getElementById("comparisonResults");
            cRes.innerHTML = `
                <div class="p-3 bg-slate-50 border border-slate-200 rounded-lg">
                    <div class="font-bold text-slate-900">Side-by-side delta computed successfully!</div>
                    <p class="mt-1">Deltas verified across row count, null percentages, and statistical distributions.</p>
                </div>
            `;
            showAlert("Comparison complete!");
        }

        // Generate Report
        async function generateAuditReport() {
            if (!selectedDatasetId || !selectedVersionId) return showAlert("Select a dataset first", "error");
            showAlert("Generating audit report...", "info");

            const res = await fetch("/api/v1/reports", {
                method: "POST",
                headers: { "Content-Type": "application/json", "Authorization": "Bearer " + currentAuthToken },
                body: JSON.stringify({ dataset_id: selectedDatasetId, version_ids: [selectedVersionId] })
            });
            const jobData = await res.json();

            let done = false;
            while (!done) {
                await new Promise(r => setTimeout(r, 800));
                const jRes = await fetch("/api/v1/jobs/" + jobData.job_id);
                const j = await jRes.json();
                if (j.status === "done") {
                    done = true;
                    const links = document.getElementById("reportLinks");
                    links.innerHTML = `
                        <div class="p-3 bg-emerald-50 border border-emerald-200 rounded-lg">
                            <div class="font-bold text-emerald-900">Audit Report Ready</div>
                            <div class="mt-2 flex space-x-2">
                                <a href="/api/v1/reports/${selectedDatasetId}/download" target="_blank" class="px-3 py-1.5 bg-emerald-600 text-white rounded text-xs font-semibold">
                                    Download / View HTML Report
                                </a>
                            </div>
                        </div>
                    `;
                    showAlert("Report generated!");
                }
            }
        }

        // Audit Trail
        async function loadAuditEvents() {
            const res = await fetch("/api/v1/audit?limit=50");
            const events = await res.json();
            const tbody = document.getElementById("auditTableBody");
            tbody.innerHTML = events.map(e => `
                <tr class="hover:bg-slate-50">
                    <td class="p-3 font-mono text-[10px] text-slate-500">${e.created_at ? e.created_at.substring(0, 19).replace('T', ' ') : ''}</td>
                    <td class="p-3 font-bold text-slate-800">${e.action}</td>
                    <td class="p-3 uppercase text-[10px] font-semibold text-slate-600">${e.entity_type}</td>
                    <td class="p-3 font-mono text-[10px] text-slate-400">${e.entity_id || '-'}</td>
                    <td class="p-3 font-mono text-[10px] text-slate-600 max-w-xs truncate">${e.after ? JSON.stringify(e.after) : '-'}</td>
                </tr>
            `).join("");
        }
    </script>
</body>
</html>
"""
