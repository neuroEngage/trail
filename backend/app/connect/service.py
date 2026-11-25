import io
import json
import os
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, inspect, text

from backend.app.platform.config import fernet, MAX_UPLOAD_SIZE_BYTES, UPLOADS_DIR
from backend.app.platform.models import Source, Dataset, DatasetVersion
from backend.app.platform.versioning import save_immutable_version_df
from backend.app.platform import audit

class ConnectError(Exception):
    pass

def detect_csv_encoding_and_separator(file_bytes: bytes) -> Tuple[str, str]:
    """Detects encoding (utf-8, latin-1, utf-16) and separator."""
    if file_bytes.startswith(b"\xff\xfe") or file_bytes.startswith(b"\xfe\xff"):
        return "utf-16", ","
    if file_bytes.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", ","

    sample = file_bytes[:16384]
    if b"\x00" in sample:
        return "utf-16", ","

    encodings = ["utf-8", "latin-1"]
    detected_encoding = "utf-8"
    for enc in encodings:
        try:
            sample.decode(enc)
            detected_encoding = enc
            break
        except UnicodeDecodeError:
            continue

    try:
        text_str = sample.decode(detected_encoding)
        delims = [",", ";", "\t", "|"]
        first_line = text_str.splitlines()[0] if text_str.splitlines() else ""
        delim_counts = {d: first_line.count(d) for d in delims}
        separator = max(delim_counts, key=delim_counts.get) if any(delim_counts.values()) else ","
    except Exception:
        separator = ","

    return detected_encoding, separator

def parse_uploaded_file(filename: str, content: bytes) -> pd.DataFrame:
    """Parses uploaded file (CSV, Excel, Parquet, JSON) into a DataFrame."""
    if len(content) == 0:
        raise ConnectError("File is empty (0 bytes).")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise ConnectError(f"File exceeds maximum allowed upload size of 500 MB.")

    ext = Path(filename).suffix.lower()
    
    try:
        if ext == ".csv":
            enc, sep = detect_csv_encoding_and_separator(content)
            df = pd.read_csv(io.BytesIO(content), encoding=enc, sep=sep, low_memory=False)
        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(io.BytesIO(content))
        elif ext == ".parquet":
            df = pd.read_parquet(io.BytesIO(content))
        elif ext == ".json":
            df = pd.read_json(io.BytesIO(content))
        else:
            raise ConnectError(f"Unsupported file format '{ext}'. Supported: .csv, .xlsx, .xls, .parquet, .json")
    except ConnectError:
        raise
    except Exception as e:
        raise ConnectError(f"Failed to parse file '{filename}': {str(e)}")

    if df.empty and len(df.columns) == 0:
        raise ConnectError("Parsed dataset has no columns or data.")
    return df

def register_file_dataset(
    db: Session,
    filename: str,
    content: bytes,
    dataset_name: Optional[str] = None,
    user_id: Optional[str] = None
) -> Tuple[Dataset, DatasetVersion]:
    """
    Parses an uploaded file, creates Dataset and Version 1 (snapshot), and saves Parquet.
    """
    df = parse_uploaded_file(filename, content)
    name = dataset_name or Path(filename).stem

    # Create Source entry
    source = Source(
        name=f"File: {filename}",
        type="file",
        status="active",
        owner_id=user_id
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    # Create Dataset entry
    dataset = Dataset(
        source_id=source.id,
        name=name,
        owner_id=user_id
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    # Save immutable v1 Parquet file
    parquet_path, row_count, col_count, checksum = save_immutable_version_df(
        dataset_id=dataset.id,
        version_number=1,
        df=df
    )

    version = DatasetVersion(
        dataset_id=dataset.id,
        number=1,
        kind="snapshot",
        parent_version_id=None,
        parquet_path=parquet_path,
        row_count=row_count,
        col_count=col_count,
        checksum=checksum,
        created_by=user_id
    )
    db.add(version)
    dataset.latest_version_id = version.id
    db.commit()
    db.refresh(version)

    # Audit log
    audit.record(
        db=db,
        actor_id=user_id,
        action="SNAPSHOT_CREATED",
        entity_type="dataset_version",
        entity_id=version.id,
        after={"dataset_id": dataset.id, "filename": filename, "rows": row_count, "cols": col_count}
    )

    return dataset, version

def encrypt_db_config(config_dict: dict) -> str:
    serialized = json.dumps(config_dict)
    return fernet.encrypt(serialized.encode("utf-8")).decode("utf-8")

def decrypt_db_config(encrypted_str: str) -> dict:
    decrypted = fernet.decrypt(encrypted_str.encode("utf-8")).decode("utf-8")
    return json.loads(decrypted)

def test_database_connection(config: dict) -> Tuple[bool, str]:
    """Tests connection to PostgreSQL server."""
    try:
        url = (
            f"postgresql+psycopg2://{config.get('username')}:{config.get('password')}@"
            f"{config.get('host')}:{config.get('port', 5432)}/{config.get('database')}"
        )
        test_engine = create_engine(url, connect_args={"connect_timeout": 5})
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Connection successful"
    except Exception as e:
        # Never leak raw credentials in error
        err_msg = str(e)
        if config.get("password"):
            err_msg = err_msg.replace(config["password"], "********")
        return False, err_msg

def discover_db_schemas(config: dict) -> Dict[str, List[str]]:
    """Lists tables grouped by schema."""
    url = (
        f"postgresql+psycopg2://{config.get('username')}:{config.get('password')}@"
        f"{config.get('host')}:{config.get('port', 5432)}/{config.get('database')}"
    )
    test_engine = create_engine(url, connect_args={"connect_timeout": 5})
    inspector = inspect(test_engine)
    result = {}
    for schema in inspector.get_schema_names():
        if schema in ["information_schema", "pg_catalog"]:
            continue
        tables = inspector.get_table_names(schema=schema)
        if tables:
            result[schema] = tables
    return result

def snapshot_database_table(
    db: Session,
    source_id: str,
    schema_name: str,
    table_name: str,
    dataset_name: Optional[str] = None,
    query: Optional[str] = None,
    user_id: Optional[str] = None
) -> Tuple[Dataset, DatasetVersion]:
    """Snapshots a table or read-only query from PostgreSQL into a Parquet dataset version."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise ConnectError(f"Source {source_id} not found.")

    config = decrypt_db_config(source.config_encrypted)
    url = (
        f"postgresql+psycopg2://{config.get('username')}:{config.get('password')}@"
        f"{config.get('host')}:{config.get('port', 5432)}/{config.get('database')}"
    )
    engine = create_engine(url, connect_args={"connect_timeout": 10})
    
    # Strictly read-only query
    sql = query or f"SELECT * FROM {schema_name}.{table_name}"
    # Guard against write queries
    prohibited = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"]
    for kw in prohibited:
        if re.search(rf"\b{kw}\b", sql, re.IGNORECASE):
            raise ConnectError(f"Security violation: SQL query must be strictly read-only SELECT. Prohibited keyword: {kw}")

    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)

    name = dataset_name or f"{schema_name}.{table_name}"
    dataset = Dataset(
        source_id=source.id,
        name=name,
        owner_id=user_id
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    parquet_path, row_count, col_count, checksum = save_immutable_version_df(
        dataset_id=dataset.id,
        version_number=1,
        df=df
    )

    version = DatasetVersion(
        dataset_id=dataset.id,
        number=1,
        kind="snapshot",
        parent_version_id=None,
        parquet_path=parquet_path,
        row_count=row_count,
        col_count=col_count,
        checksum=checksum,
        created_by=user_id
    )
    db.add(version)
    dataset.latest_version_id = version.id
    db.commit()
    db.refresh(version)

    audit.record(
        db=db,
        actor_id=user_id,
        action="SNAPSHOT_CREATED",
        entity_type="dataset_version",
        entity_id=version.id,
        after={"dataset_id": dataset.id, "source_id": source.id, "rows": row_count, "cols": col_count}
    )

    return dataset, version
