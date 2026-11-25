import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from backend.app.platform.database import Base

def gen_uuid():
    return str(uuid.uuid4())

def utc_now():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False)  # analyst, approver, admin
    created_at = Column(DateTime, default=utc_now)

class Source(Base):
    __tablename__ = "sources"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # file, postgres
    config_encrypted = Column(Text, nullable=True)  # Fernet encrypted connection payload
    status = Column(String, default="active")  # active, disconnected
    owner_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    datasets = relationship("Dataset", back_populates="source", cascade="all, delete-orphan")

class Dataset(Base):
    __tablename__ = "datasets"
    id = Column(String, primary_key=True, default=gen_uuid)
    source_id = Column(String, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    name = Column(String, nullable=False)
    latest_version_id = Column(String, nullable=True)
    owner_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    source = relationship("Source", back_populates="datasets")
    versions = relationship("DatasetVersion", back_populates="dataset", cascade="all, delete-orphan")
    rules = relationship("Rule", back_populates="dataset", cascade="all, delete-orphan")

class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    id = Column(String, primary_key=True, default=gen_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    number = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)  # snapshot, cleaned
    parent_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=True)
    parquet_path = Column(String, nullable=False)
    row_count = Column(Integer, default=0)
    col_count = Column(Integer, default=0)
    checksum = Column(String, nullable=False)  # sha256 checksum of the Parquet file
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    dataset = relationship("Dataset", back_populates="versions")
    profile = relationship("Profile", back_populates="version", uselist=False, cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="version", cascade="all, delete-orphan")

class Job(Base):
    __tablename__ = "jobs"
    id = Column(String, primary_key=True, default=gen_uuid)
    type = Column(String, nullable=False)  # profile, assess, apply, report, snapshot
    entity_id = Column(String, nullable=True)
    status = Column(String, default="queued")  # queued, running, done, failed, cancelled
    progress = Column(Integer, default=0)  # 0-100
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    finished_at = Column(DateTime, nullable=True)

class Profile(Base):
    __tablename__ = "profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    dataset_version_id = Column(String, ForeignKey("dataset_versions.id", ondelete="CASCADE"), unique=True, nullable=False)
    results_json = Column(Text, nullable=False)  # Column profile metadata & stats
    created_at = Column(DateTime, default=utc_now)

    version = relationship("DatasetVersion", back_populates="profile")

class Rule(Base):
    __tablename__ = "rules"
    id = Column(String, primary_key=True, default=gen_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    dimension = Column(String, nullable=False)  # completeness, validity, uniqueness, consistency, accuracy, timeliness
    expression = Column(Text, nullable=False)
    severity = Column(String, default="medium")  # low, medium, high, critical
    enabled = Column(Boolean, default=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    dataset = relationship("Dataset", back_populates="rules")

class Assessment(Base):
    __tablename__ = "assessments"
    id = Column(String, primary_key=True, default=gen_uuid)
    dataset_version_id = Column(String, ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False)
    scores_json = Column(Text, nullable=False)  # JSON per-dimension scores
    overall = Column(Float, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    version = relationship("DatasetVersion", back_populates="assessments")
    issues = relationship("Issue", back_populates="assessment", cascade="all, delete-orphan")

class Issue(Base):
    __tablename__ = "issues"
    id = Column(String, primary_key=True, default=gen_uuid)
    assessment_id = Column(String, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    type = Column(String, nullable=False)  # duplicate, disguised_null, invalid_format, outlier, inconsistent_category, rule_violation
    dimension = Column(String, nullable=False)
    column = Column(String, nullable=True)
    severity = Column(String, default="medium")
    affected_count = Column(Integer, default=0)
    why_text = Column(Text, nullable=False)
    status = Column(String, default="open")  # open, valid, ignored
    created_at = Column(DateTime, default=utc_now)

    assessment = relationship("Assessment", back_populates="issues")
    records = relationship("IssueRecord", back_populates="issue", cascade="all, delete-orphan")
    actions = relationship("Action", back_populates="issue", cascade="all, delete-orphan")

class IssueRecord(Base):
    __tablename__ = "issue_records"
    id = Column(String, primary_key=True, default=gen_uuid)
    issue_id = Column(String, ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    row_index = Column(Integer, nullable=False)
    column = Column(String, nullable=True)
    value = Column(Text, nullable=True)

    issue = relationship("Issue", back_populates="records")

class Action(Base):
    __tablename__ = "actions"
    id = Column(String, primary_key=True, default=gen_uuid)
    issue_id = Column(String, ForeignKey("issues.id", ondelete="CASCADE"), nullable=True)
    dataset_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=True)
    type = Column(String, nullable=False)  # drop_duplicates, fill_null, standardise_category, null_out, trim_whitespace
    config_json = Column(Text, nullable=False)
    status = Column(String, default="proposed")  # proposed, approved, applied, undone
    rows_affected = Column(Integer, default=0)
    proposed_by = Column(String, ForeignKey("users.id"), nullable=True)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    result_version_id = Column(String, ForeignKey("dataset_versions.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    issue = relationship("Issue", back_populates="actions")

class Comparison(Base):
    __tablename__ = "comparisons"
    id = Column(String, primary_key=True, default=gen_uuid)
    version_a = Column(String, ForeignKey("dataset_versions.id"), nullable=False)
    version_b = Column(String, ForeignKey("dataset_versions.id"), nullable=False)
    metrics_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now)

class Report(Base):
    __tablename__ = "reports"
    id = Column(String, primary_key=True, default=gen_uuid)
    dataset_id = Column(String, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    version_ids = Column(Text, nullable=False)  # JSON list
    sections = Column(Text, nullable=False)  # JSON list
    format = Column(String, default="html")  # pdf, html, excel
    file_path = Column(String, nullable=True)
    share_token = Column(String, unique=True, nullable=True, index=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(String, primary_key=True, default=gen_uuid)
    actor_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
