from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = (
    "postgresql://postgres.qdgmyyimyijdquodwjqv:aimeloic132@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"
)

engine = create_engine(
    DATABASE_URL,
    echo=True,
    pool_pre_ping=True,
    connect_args={
        "sslmode": "require",
        "options":"-c statement_timeout=5000"
    }
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
