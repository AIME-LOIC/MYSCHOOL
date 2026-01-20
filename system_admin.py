# system_admin.py
"""
System Admin Module
Manages multiple schools with dynamic table creation per school
"""
from sqlalchemy import Column, Integer, String, DateTime, create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
from config import Base

class School(Base):
    """System level - stores all registered schools"""
    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, index=True)
    school_name = Column(String(255), unique=True, nullable=False, index=True)
    school_code = Column(String(50), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Integer, default=1)
    
    def __repr__(self):
        return f"<School(id={self.id}, name={self.school_name}, code={self.school_code})>"


# Dictionary to store school-specific database engines and sessions
school_databases = {}


def create_school_database(school_name: str, school_code: str):
    """
    Create a new database/schema for a school
    Stores connection info globally
    """
    # You can customize this based on your database system
    # For PostgreSQL: create a new schema
    # For SQLite: create a new database file
    # For MySQL: create a new database
    
    if school_name in school_databases:
        return {"status": "error", "message": f"School '{school_name}' database already exists"}
    
    try:
        # Store school database connection info
        school_databases[school_name] = {
            "school_code": school_code,
            "created_at": datetime.utcnow(),
            "active": True
        }
        
        return {
            "status": "success",
            "message": f"Database for school '{school_name}' created successfully",
            "school_name": school_name,
            "school_code": school_code
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_school_database_info(school_name: str):
    """Get database information for a specific school"""
    if school_name not in school_databases:
        return None
    return school_databases[school_name]


def list_all_schools():
    """List all registered schools"""
    return school_databases


def delete_school_database(school_name: str):
    """Delete a school's database"""
    if school_name not in school_databases:
        return {"status": "error", "message": f"School '{school_name}' not found"}
    
    try:
        del school_databases[school_name]
        return {"status": "success", "message": f"School '{school_name}' database deleted"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
