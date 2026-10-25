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
# Vercel runs many short-lived serverless instances. Keep each instance's
# connection footprint small: Supabase session-mode poolers commonly cap the
# total number of client connections, and a large per-instance pool exhausts it.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=1,
    max_overflow=0,
    pool_timeout=5,
    pool_recycle=300,
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
    _ensure_student_canteen_card_id_column()
    _ensure_parent_meeting_columns()
    _ensure_school_last_promoted_at_column()
    _ensure_marks_teacher_id_column()
    _ensure_teachers_email_column()
    _ensure_teachers_is_dos_column()

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




def _ensure_marks_teacher_id_column():
    """Record which teacher entered or updated each mark."""
    from sqlalchemy import text, inspect

    with engine.connect() as connection:
        inspector = inspect(connection)
        if "student_marks" not in inspector.get_table_names():
            return
        columns = {col['name'] for col in inspector.get_columns('student_marks')}
        if 'teacher_id' not in columns:
            try:
                connection.execute(text("ALTER TABLE student_marks ADD COLUMN teacher_id INTEGER"))
                connection.commit()
                print("✓ Added teacher_id column to student_marks table")
            except Exception as e:
                print(f"Warning: Could not add teacher_id column to student_marks: {e}")
                connection.rollback()


def _ensure_teachers_email_column():
    """Teachers sign up with an email address as part of their school profile."""
    from sqlalchemy import text, inspect

    with engine.connect() as connection:
        inspector = inspect(connection)
        if "teachers" not in inspector.get_table_names():
            return
        columns = {col['name'] for col in inspector.get_columns('teachers')}
        if 'email' not in columns:
            try:
                connection.execute(text("ALTER TABLE teachers ADD COLUMN email VARCHAR(254)"))
                connection.commit()
                print("✓ Added email column to teachers table")
            except Exception as e:
                print(f"Warning: Could not add email column to teachers: {e}")
                connection.rollback()


def _ensure_teachers_is_dos_column():
    """Dean of Studies flag: the teacher allowed to build the school timetables."""
    from sqlalchemy import text, inspect

    with engine.connect() as connection:
        inspector = inspect(connection)
        if "teachers" not in inspector.get_table_names():
            return
        columns = {col['name'] for col in inspector.get_columns('teachers')}
        if 'is_dos' not in columns:
            try:
                connection.execute(text("ALTER TABLE teachers ADD COLUMN is_dos INTEGER NOT NULL DEFAULT 0"))
                connection.commit()
                print("✓ Added is_dos column to teachers table")
            except Exception as e:
                print(f"Warning: Could not add is_dos column to teachers: {e}")
                connection.rollback()


def _ensure_parent_meeting_columns():
    """Safely migrate existing visits tables for parent meeting submissions."""
    from sqlalchemy import text, inspect

    with engine.connect() as connection:
        inspector = inspect(connection)
        if "visits" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("visits")}
        required = {
            "parent_name": "VARCHAR(120)",
            "parent_relationship": "VARCHAR(20)",
            "parent_phone": "VARCHAR(30)",
        }
        for column_name, column_type in required.items():
            if column_name not in columns:
                connection.execute(text(f"ALTER TABLE visits ADD COLUMN {column_name} {column_type}"))
                print(f"✓ Added {column_name} column to visits table")
        connection.commit()


def _ensure_student_canteen_card_id_column():
    """Add school-issued five-digit canteen IDs to existing student tables."""
    from sqlalchemy import text, inspect
    with engine.connect() as connection:
        inspector = inspect(connection)
        if "students" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("students")}
        if "canteen_card_id" not in columns:
            connection.execute(text("ALTER TABLE students ADD COLUMN canteen_card_id VARCHAR(5)"))
            connection.commit()
        # A code must be unique within a school. NULL values for unissued cards remain allowed.
        try:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_students_school_canteen_card_id ON students (school_id, canteen_card_id)"))
            connection.commit()
        except Exception:
            connection.rollback()
