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
    echo=False,
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
    _ensure_products_table()
    _ensure_student_school_id_column()
    _ensure_visit_transport_columns()
    _ensure_student_is_active_column()
    _ensure_student_password_columns()
    _ensure_parent_link_code_column()
    _ensure_school_last_promoted_at_column()

def _ensure_products_table():
    """Create products table if it doesn't exist"""
    from sqlalchemy import text, inspect
    with engine.connect() as connection:
        inspector = inspect(connection)
        if 'products' not in inspector.get_table_names():
            try:
                connection.execute(text("""
                    CREATE TABLE products (
                        product_id SERIAL PRIMARY KEY,
                        product_name VARCHAR(100) NOT NULL,
                        product_price INTEGER NOT NULL,
                        school_id INTEGER REFERENCES schools(id)
                    )
                """))
                connection.commit()
                print("✓ Created products table")
            except Exception as e:
                print(f"Warning: Could not create products table: {e}")
                connection.rollback()

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

def _ensure_visit_transport_columns():
    """Add transport/car columns to visits table if they don't exist"""
    from sqlalchemy import text, inspect

    with engine.connect() as connection:
        inspector = inspect(connection)

        if 'visits' in inspector.get_table_names():
            columns = {col['name'] for col in inspector.get_columns('visits')}

            if 'movement_method' not in columns:
                try:
                    connection.execute(text("ALTER TABLE visits ADD COLUMN movement_method VARCHAR(20)"))
                    connection.commit()
                    print("✓ Added movement_method column to visits table")
                except Exception as e:
                    print(f"Warning: Could not add movement_method column: {e}")
                    connection.rollback()

            if 'arrival_plate_number' not in columns:
                try:
                    connection.execute(text("ALTER TABLE visits ADD COLUMN arrival_plate_number VARCHAR(30)"))
                    connection.commit()
                    print("✓ Added arrival_plate_number column to visits table")
                except Exception as e:
                    print(f"Warning: Could not add arrival_plate_number column: {e}")
                    connection.rollback()

            if 'assigned_plate_number' not in columns:
                try:
                    connection.execute(text("ALTER TABLE visits ADD COLUMN assigned_plate_number VARCHAR(30)"))
                    connection.commit()
                    print("✓ Added assigned_plate_number column to visits table")
                except Exception as e:
                    print(f"Warning: Could not add assigned_plate_number column: {e}")
                    connection.rollback()

            if 'created_at' not in columns:
                try:
                    connection.execute(text("ALTER TABLE visits ADD COLUMN created_at TIMESTAMP DEFAULT NOW()"))
                    connection.commit()
                    print("✓ Added created_at column to visits table")
                except Exception as e:
                    print(f"Warning: Could not add created_at column: {e}")
                    connection.rollback()

def _ensure_student_is_active_column():
    """Add is_active column to students table if it doesn't exist (for promotion/archive support)"""
    from sqlalchemy import text, inspect

    with engine.connect() as connection:
        inspector = inspect(connection)

        if 'students' in inspector.get_table_names():
            columns = {col['name'] for col in inspector.get_columns('students')}

            if 'is_active' not in columns:
                try:
                    print("Adding is_active column to students table...")
                    # Default 1 so all existing students are considered active (still in school)
                    connection.execute(text("ALTER TABLE students ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"))
                    connection.commit()
                    print("✓ Added is_active column")
                except Exception as e:
                    print(f"Warning: Could not add is_active column: {e}")
                    connection.rollback()

def _ensure_school_last_promoted_at_column():
    """Add last_promoted_at column to schools table if it doesn't exist (promotion lock)"""
    from sqlalchemy import text, inspect

    with engine.connect() as connection:
        inspector = inspect(connection)

        if 'schools' in inspector.get_table_names():
            columns = {col['name'] for col in inspector.get_columns('schools')}

            if 'last_promoted_at' not in columns:
                try:
                    connection.execute(text("ALTER TABLE schools ADD COLUMN last_promoted_at TIMESTAMP"))
                    connection.commit()
                    print("✓ Added last_promoted_at column to schools table")
                except Exception as e:
                    print(f"Warning: Could not add last_promoted_at column: {e}")
                    connection.rollback()

def _ensure_student_password_columns():
    """Add student portal authentication columns to existing PostgreSQL databases."""
    from sqlalchemy import text, inspect

    with engine.connect() as connection:
        inspector = inspect(connection)
        if "students" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("students")}
        if "password_hash" not in columns:
            connection.execute(text("ALTER TABLE students ADD COLUMN password_hash VARCHAR(255)"))
        if "must_change_password" not in columns:
            connection.execute(text("ALTER TABLE students ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1"))
        connection.commit()


def _ensure_parent_link_code_column():
    """Add the school-issued parent verification code column to existing student tables."""
    from sqlalchemy import text, inspect
    with engine.connect() as connection:
        inspector = inspect(connection)
        if "students" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("students")}
        if "parent_link_code" not in columns:
            connection.execute(text("ALTER TABLE students ADD COLUMN parent_link_code VARCHAR(32)"))
            connection.commit()
