"""
Migration script to add school_id column to existing students table
Run this once to update your Supabase schema
"""
from sqlalchemy import text
from config import engine

def migrate():
    """Add school_id column to students table"""
    with engine.connect() as connection:
        try:
            # Check if school_id column already exists
            result = connection.execute(
                text("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='students' AND column_name='school_id'
                    )
                """)
            )
            column_exists = result.scalar()
            
            if column_exists:
                print("✓ school_id column already exists in students table")
                return
            
            # Add school_id column with default value
            print("Adding school_id column to students table...")
            connection.execute(
                text("""
                    ALTER TABLE students 
                    ADD COLUMN school_id INTEGER DEFAULT 1
                """)
            )
            connection.commit()
            print("✓ Successfully added school_id column to students table")
            
            # Add foreign key constraint
            print("Adding foreign key constraint...")
            connection.execute(
                text("""
                    ALTER TABLE students 
                    ADD CONSTRAINT fk_students_school_id 
                    FOREIGN KEY (school_id) REFERENCES schools(id)
                """)
            )
            connection.commit()
            print("✓ Successfully added foreign key constraint")
            
            # Make school_id NOT NULL after default is applied
            print("Making school_id NOT NULL...")
            connection.execute(
                text("""
                    ALTER TABLE students 
                    ALTER COLUMN school_id SET NOT NULL
                """)
            )
            connection.commit()
            print("✓ Successfully made school_id NOT NULL")
            
        except Exception as e:
            print(f"✗ Migration failed: {e}")
            connection.rollback()
            raise

if __name__ == "__main__":
    migrate()
