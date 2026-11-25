import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Callable, Any, Dict, Optional
from sqlalchemy.orm import Session

from backend.app.platform.database import SessionLocal
from backend.app.platform.models import Job

# In-memory cancel registry for running tasks
CANCELLED_JOB_IDS = set()

def enqueue_job(job_type: str, entity_id: Optional[str] = None) -> Job:
    """Creates a new queued job in the database."""
    db: Session = SessionLocal()
    try:
        job = Job(
            type=job_type,
            entity_id=entity_id,
            status="queued",
            progress=0,
            created_at=datetime.now(timezone.utc)
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job
    finally:
        db.close()

def is_job_cancelled(job_id: str) -> bool:
    if job_id in CANCELLED_JOB_IDS:
        return True
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        return job.status == "cancelled" if job else False
    finally:
        db.close()

def update_job_progress(job_id: str, progress: int, status: Optional[str] = None):
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job and job.status != "cancelled":
            job.progress = min(100, max(0, progress))
            if status:
                job.status = status
            db.commit()
    finally:
        db.close()

def mark_job_done(job_id: str):
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job and job.status != "cancelled":
            job.status = "done"
            job.progress = 100
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()

def mark_job_failed(job_id: str, error_message: str):
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job and job.status != "cancelled":
            job.status = "failed"
            job.error = error_message
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()

def cancel_job(job_id: str) -> bool:
    CANCELLED_JOB_IDS.add(job_id)
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return True
        return False
    finally:
        db.close()

def run_in_background(target: Callable, *args, **kwargs):
    """Spawns target in a separate thread for local async execution."""
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
