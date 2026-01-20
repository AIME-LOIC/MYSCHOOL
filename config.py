from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Get DATABASE_URL from environment (Supabase PostgreSQL)
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set. Please add it to your .env file.")

# Configure Supabase PostgreSQL connection
engine = create_engine(
    DATABASE_URL,
    echo=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args={
        "sslmode": "require",
    }
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Initialize database tables - creates all tables defined in models"""
    Base.metadata.create_all(bind=engine)
    
    # Ensure school_id column exists in students table (for existing databases)
    _ensure_student_school_id_column()

def _ensure_student_school_id_column():
    """Add school_id column to students table if it doesn't exist"""
    from sqlalchemy import text, inspect
    
    with engine.connect() as connection:
        inspector = inspect(connection)
        
        # Get columns for students table
        if 'students' in inspector.get_table_names():
            columns = {col['name'] for col in inspector.get_columns('students')}
            
            if 'school_id' not in columns:
                try:
                    print("Adding school_id column to students table...")
                    connection.execute(text("ALTER TABLE students ADD COLUMN school_id INTEGER DEFAULT 1"))
                    connection.commit()
                    print("✓ Added school_id column")
                    
                    # Try to add foreign key
                    try:
                        connection.execute(text("""
                            ALTER TABLE students 
                            ADD CONSTRAINT fk_students_school_id 
                            FOREIGN KEY (school_id) REFERENCES schools(id)
                        """))
                        connection.commit()
                    except:
                        pass
                    
                    # Make it NOT NULL
                    try:
                        connection.execute(text("ALTER TABLE students ALTER COLUMN school_id SET NOT NULL"))
                        connection.commit()
                    except:
                        pass
                        
                except Exception as e:
                    print(f"Warning: Could not add school_id column: {e}")
                    connection.rollback()
