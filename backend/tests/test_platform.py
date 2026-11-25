import os
import pytest
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.platform.database import Base, init_db, SessionLocal
from backend.app.platform.models import User, AuditEvent, Job, DatasetVersion
from backend.app.platform.auth import hash_password, verify_password, create_access_token
from backend.app.platform.versioning import (
    save_immutable_version_df,
    verify_version_checksum,
    VersionImmutableError
)
from backend.app.platform import audit, jobs

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield

def test_auth_password_and_token():
    hashed = hash_password("secret123")
    assert verify_password("secret123", hashed)
    assert not verify_password("wrong", hashed)

    token = create_access_token({"sub": "user-123", "role": "analyst"})
    assert isinstance(token, str) and len(token) > 20

def test_versioning_immutability(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.platform.versioning.DATASETS_DIR", tmp_path)
    
    df = pd.DataFrame({"colA": [1, 2, 3], "colB": ["x", "y", "z"]})
    dataset_id = "test-dataset-01"
    
    # Save v1
    path_str, row_count, col_count, checksum = save_immutable_version_df(dataset_id, 1, df)
    assert row_count == 3
    assert col_count == 2
    assert len(checksum) == 64

    # Verify checksum
    v = DatasetVersion(
        dataset_id=dataset_id,
        number=1,
        kind="snapshot",
        parquet_path=path_str,
        row_count=row_count,
        col_count=col_count,
        checksum=checksum
    )
    assert verify_version_checksum(v)

    # Attempting to overwrite v1 must fail
    with pytest.raises(VersionImmutableError):
        save_immutable_version_df(dataset_id, 1, df)

def test_audit_record():
    db = SessionLocal()
    try:
        ev = audit.record(
            db=db,
            actor_id="admin-01",
            action="CREATE_DATASET",
            entity_type="dataset",
            entity_id="ds-123",
            before=None,
            after={"name": "Sales Data"}
        )
        assert ev.id is not None
        assert ev.action == "CREATE_DATASET"
        assert '"Sales Data"' in ev.after_json
    finally:
        db.close()

def test_jobs_lifecycle():
    job = jobs.enqueue_job("profile", "ds-123")
    assert job.status == "queued"
    assert job.progress == 0

    jobs.update_job_progress(job.id, 50, "running")
    jobs.cancel_job(job.id)
    assert jobs.is_job_cancelled(job.id)
