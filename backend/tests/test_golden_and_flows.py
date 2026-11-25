import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.platform.database import init_db, SessionLocal
from backend.app.platform.models import User, Dataset, DatasetVersion, Assessment, Issue
from backend.app.connect import service as connect_service
from backend.app.quality import service as quality_service
from backend.app.cleaner import service as cleaner_service
from backend.app.compare import service as compare_service
from backend.app.reports import service as reports_service

client = TestClient(app)

TESTDATA_DIR = Path(__file__).resolve().parent.parent.parent / "testdata"

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    init_db()

def get_auth_token(role="approver"):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.role == role).first()
        from backend.app.platform.auth import create_access_token
        return create_access_token({"sub": user.id, "email": user.email, "role": user.role})
    finally:
        db.close()

def test_connect_edge_cases():
    edge_dir = TESTDATA_DIR / "edge"
    db = SessionLocal()
    try:
        # Empty file should fail validation
        with pytest.raises(connect_service.ConnectError):
            connect_service.parse_uploaded_file("empty.csv", (edge_dir / "empty.csv").read_bytes())

        # Latin-1 encoded CSV
        df_latin1 = connect_service.parse_uploaded_file("latin1.csv", (edge_dir / "latin1.csv").read_bytes())
        assert len(df_latin1) == 2

        # UTF-16 encoded CSV
        df_utf16 = connect_service.parse_uploaded_file("utf16.csv", (edge_dir / "utf16.csv").read_bytes())
        assert len(df_utf16) == 2

        # Mixed types CSV
        df_mixed = connect_service.parse_uploaded_file("mixed_types.csv", (edge_dir / "mixed_types.csv").read_bytes())
        assert len(df_mixed) == 5
    finally:
        db.close()

def test_golden_clean_dataset():
    db = SessionLocal()
    try:
        clean_bytes = (TESTDATA_DIR / "golden_clean.csv").read_bytes()
        ds, ver = connect_service.register_file_dataset(db, "golden_clean.csv", clean_bytes, "Golden Clean Dataset")
        assert ver.row_count == 1000

        # Run Assessment
        assessment = quality_service.run_quality_assessment(db, ver.id)
        # Clean dataset overall score must be >= 98 and zero high-severity issues (per F4 acceptance criteria)
        assert assessment.overall >= 98.0
        
        issues = db.query(Issue).filter(Issue.assessment_id == assessment.id).all()
        high_issues = [i for i in issues if i.severity in ["high", "critical"]]
        assert len(high_issues) == 0
    finally:
        db.close()

def test_golden_dirty_dataset_detection():
    db = SessionLocal()
    try:
        dirty_bytes = (TESTDATA_DIR / "golden_dirty.csv").read_bytes()
        answer_key = json.loads((TESTDATA_DIR / "golden_dirty_answer_key.json").read_text(encoding="utf-8"))
        
        ds, ver = connect_service.register_file_dataset(db, "golden_dirty.csv", dirty_bytes, "Golden Dirty Dataset")
        assert ver.row_count == 1050

        # Run Quality Assessment
        assessment = quality_service.run_quality_assessment(db, ver.id)
        
        # Verify overall score is visibly lower than clean
        assert assessment.overall < 95.0
        
        issues = db.query(Issue).filter(Issue.assessment_id == assessment.id).all()
        issue_types = {i.type: i for i in issues}

        # Check plant detection summary:
        # 1. Duplicates
        assert "duplicate" in issue_types
        assert issue_types["duplicate"].affected_count >= 50

        # 2. Disguised nulls (N/A in city, - in phone, 999 in age)
        disguised_issues = [i for i in issues if i.type == "disguised_null"]
        disguised_cols = {i.column for i in disguised_issues}
        assert "city" in disguised_cols or "phone" in disguised_cols or "age" in disguised_cols

        # 3. Invalid email format
        email_issues = [i for i in issues if i.type == "invalid_format"]
        assert len(email_issues) > 0
        assert email_issues[0].affected_count >= 18  # planted 20

        # 4. Outliers in amount
        outlier_issues = [i for i in issues if i.type == "outlier"]
        assert len(outlier_issues) > 0
        assert outlier_issues[0].column == "amount"
        assert outlier_issues[0].affected_count >= 13  # planted 15

        # 5. Inconsistent category (Mumbai variants)
        category_issues = [i for i in issues if i.type == "inconsistent_category"]
        assert len(category_issues) > 0
        assert category_issues[0].column == "city"

        # 6. Rule violation (end before start)
        rule_issues = [i for i in issues if i.type == "rule_violation"]
        assert len(rule_issues) > 0
        assert rule_issues[0].affected_count >= 24  # planted 25

    finally:
        db.close()

def test_controlled_cleaning_f6_workflow():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.role == "approver").first()
        dirty_bytes = (TESTDATA_DIR / "golden_dirty.csv").read_bytes()
        ds, v1 = connect_service.register_file_dataset(db, "golden_dirty.csv", dirty_bytes, "Cleaning Test DS", user_id=user.id)
        parent_checksum = v1.checksum

        # Propose Action: drop_duplicates
        action = cleaner_service.propose_action(
            db=db,
            issue_id=None,
            version_id=v1.id,
            action_type="drop_duplicates",
            config={"keep": "first"},
            user_id=user.id
        )
        assert action.rows_affected == 50

        # Preview Action
        preview = cleaner_service.preview_action(db, action.id)
        assert preview["rows_affected"] == 50
        assert preview["preview_row_count"] == 1000

        # Approve Action (approver role)
        action = cleaner_service.approve_action(db, action.id, user.id)
        assert action.status == "approved"

        # Apply Action -> Generates Version 2
        v2 = cleaner_service.apply_action_new_version(db, action.id, user.id)
        assert v2.number == 2
        assert v2.row_count == 1000
        assert v2.parent_version_id == v1.id

        # Verify parent checksum was NOT modified (immutability)
        v1_recheck = db.query(DatasetVersion).filter(DatasetVersion.id == v1.id).first()
        assert v1_recheck.checksum == parent_checksum

        # Compare v1 vs v2
        comp = compare_service.run_comparison(db, v1.id, v2.id)
        res = json.loads(comp.result_json)
        assert res["row_delta"] == -50

        # Generate Report
        report = reports_service.create_report(
            db=db,
            dataset_id=ds.id,
            version_ids=[v1.id, v2.id],
            sections=["profile", "issues", "actions", "comparison"],
            user_id=user.id
        )
        assert Path(report.file_path).exists()
        assert report.share_token is not None

    finally:
        db.close()
