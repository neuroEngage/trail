import hashlib
import os
from pathlib import Path
from typing import Optional, Tuple
import pandas as pd
import pyarrow.parquet as pq

from backend.app.platform.config import DATASETS_DIR
from backend.app.platform.models import DatasetVersion

class VersionImmutableError(Exception):
    """Raised when an attempt is made to overwrite an existing immutable dataset version."""
    pass

def compute_file_checksum(file_path: Path) -> str:
    """Computes SHA-256 checksum of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()

def verify_version_checksum(version: DatasetVersion) -> bool:
    """Verifies that the stored Parquet file matches the recorded checksum."""
    path = Path(version.parquet_path)
    if not path.exists():
        return False
    return compute_file_checksum(path) == version.checksum

def get_dataset_version_path(dataset_id: str, version_number: int) -> Path:
    target_dir = DATASETS_DIR / dataset_id
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"v{version_number}.parquet"

def save_immutable_version_df(
    dataset_id: str,
    version_number: int,
    df: pd.DataFrame
) -> Tuple[str, int, int, str]:
    """
    Saves a DataFrame as an immutable Parquet file.
    Raises VersionImmutableError if the version file already exists.
    Returns (parquet_path, row_count, col_count, checksum).
    """
    target_path = get_dataset_version_path(dataset_id, version_number)
    if target_path.exists():
        raise VersionImmutableError(
            f"Dataset version {version_number} already exists at {target_path}. Dataset versions are immutable."
        )

    # Write to temp file first, then atomically rename
    tmp_path = target_path.with_suffix(".tmp.parquet")
    try:
        df.to_parquet(tmp_path, index=False, engine="pyarrow")
        checksum = compute_file_checksum(tmp_path)
        os.replace(tmp_path, target_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    row_count = len(df)
    col_count = len(df.columns)
    return str(target_path), row_count, col_count, checksum

def read_version_df(version: DatasetVersion) -> pd.DataFrame:
    """Reads a dataset version's parquet file into a DataFrame."""
    path = Path(version.parquet_path)
    if not path.exists():
        raise FileNotFoundError(f"Version file {path} not found.")
    return pd.read_parquet(path, engine="pyarrow")
