from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
from sqlalchemy.orm import Session
from fastapi import HTTPException

from backend.app.platform.models import Dataset, DatasetVersion, Source
from backend.app.platform.versioning import read_version_df

def list_datasets(db: Session) -> List[Dict[str, Any]]:
    datasets = db.query(Dataset).order_by(Dataset.created_at.desc()).all()
    results = []
    for ds in datasets:
        latest = db.query(DatasetVersion).filter(DatasetVersion.id == ds.latest_version_id).first()
        results.append({
            "id": ds.id,
            "name": ds.name,
            "source_id": ds.source_id,
            "latest_version_id": ds.latest_version_id,
            "latest_version_number": latest.number if latest else 1,
            "row_count": latest.row_count if latest else 0,
            "col_count": latest.col_count if latest else 0,
            "created_at": ds.created_at.isoformat() if ds.created_at else None
        })
    return results

def get_dataset_metadata(db: Session, dataset_id: str) -> Dict[str, Any]:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Dataset not found", "details": {}}})
    
    source = db.query(Source).filter(Source.id == dataset.source_id).first() if dataset.source_id else None
    latest = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first()
    
    size_bytes = 0
    if latest and Path(latest.parquet_path).exists():
        size_bytes = Path(latest.parquet_path).stat().st_size

    return {
        "id": dataset.id,
        "name": dataset.name,
        "source_id": dataset.source_id,
        "source_name": source.name if source else "Direct Upload",
        "source_type": source.type if source else "file",
        "latest_version_id": dataset.latest_version_id,
        "latest_version_number": latest.number if latest else 1,
        "row_count": latest.row_count if latest else 0,
        "col_count": latest.col_count if latest else 0,
        "file_size_bytes": size_bytes,
        "owner_id": dataset.owner_id,
        "created_at": dataset.created_at.isoformat() if dataset.created_at else None
    }

def list_dataset_versions(db: Session, dataset_id: str) -> List[Dict[str, Any]]:
    versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.number.desc()).all()
    return [
        {
            "id": v.id,
            "dataset_id": v.dataset_id,
            "number": v.number,
            "kind": v.kind,
            "parent_version_id": v.parent_version_id,
            "row_count": v.row_count,
            "col_count": v.col_count,
            "checksum": v.checksum,
            "created_at": v.created_at.isoformat() if v.created_at else None
        }
        for v in versions
    ]

def get_version_columns(db: Session, version_id: str) -> List[Dict[str, Any]]:
    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Version not found", "details": {}}})
    
    df = read_version_df(version)
    columns_info = []
    for col in df.columns:
        dtype = str(df[col].dtype)
        # Infer standard semantic type approximation
        if "int" in dtype:
            inferred = "integer"
        elif "float" in dtype:
            inferred = "float"
        elif "datetime" in dtype:
            inferred = "datetime"
        elif "bool" in dtype:
            inferred = "boolean"
        else:
            inferred = "string"

        columns_info.append({
            "name": col,
            "dtype": dtype,
            "inferred_type": inferred,
            "null_count": int(df[col].isna().sum()),
            "distinct_count": int(df[col].nunique(dropna=True))
        })
    return columns_info

def get_version_rows(
    db: Session,
    version_id: str,
    page: int = 1,
    page_size: int = 50,
    sort_by: Optional[str] = None,
    sort_desc: bool = False
) -> Dict[str, Any]:
    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Version not found", "details": {}}})
    
    df = read_version_df(version)
    total_rows = len(df)
    
    if sort_by and sort_by in df.columns:
        df = df.sort_values(by=sort_by, ascending=not sort_desc)

    start = (page - 1) * page_size
    end = start + page_size
    sliced = df.iloc[start:end].copy()
    
    # Convert dataframe to JSON friendly dict, replacing NaN with None
    records = sliced.where(pd.notnull(sliced), None).to_dict(orient="records")
    # Attach row index to each record for UI traceability
    for idx, r in enumerate(records):
        r["__row_index__"] = start + idx

    return {
        "version_id": version_id,
        "page": page,
        "page_size": page_size,
        "total_rows": total_rows,
        "total_pages": (total_rows + page_size - 1) // page_size if total_rows > 0 else 1,
        "columns": list(df.columns),
        "rows": records
    }
