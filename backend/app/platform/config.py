import os
from pathlib import Path
from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DATASETS_DIR = DATA_DIR / "datasets"
REPORTS_DIR = DATA_DIR / "reports"
UPLOADS_DIR = DATA_DIR / "uploads"

# Ensure directories exist
for d in [DATA_DIR, DATASETS_DIR, REPORTS_DIR, UPLOADS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'metadata.db'}")

# Security
SECRET_KEY = os.getenv("SECRET_KEY", "dq-workbench-super-secret-key-32-chars-min-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# Fernet encryption key for external DB credentials
# If not supplied, generate a consistent default for dev or use env
FERNET_KEY = os.getenv("FERNET_KEY", Fernet.generate_key().decode())
fernet = Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)

MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB (D13)
