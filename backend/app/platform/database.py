from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.app.platform.config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    from backend.app.platform import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    
    # Seed default users if none exist
    db = SessionLocal()
    try:
        from backend.app.platform.models import User
        from backend.app.platform.auth import hash_password
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
