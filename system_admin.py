"""
System Admin Module
Manages multiple schools with dynamic table creation per school
Includes access control: manual toggle + optional open/close schedule
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

    # Access control
    is_open = Column(Integer, default=1)          # 1 = open, 0 = closed (manual toggle)
    open_at = Column(DateTime, nullable=True)     # scheduled open time (optional)
    close_at = Column(DateTime, nullable=True)    # scheduled close time (optional)

    def __repr__(self):
        return f"<School(id={self.id}, name={self.school_name}, code={self.school_code})>"

    def access_status(self) -> dict:
        """
        Returns the effective access state for this school.
        Schedule overrides the manual toggle when active.
        """
        # 1. Use timezone-aware UTC now, then strip it to make it safe for naive DB columns
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Extract values cleanly
        open_at = self.open_at
        close_at = self.close_at

        # Helper string conversion safely appending Z for the frontend
        def to_iso(dt):
            return dt.isoformat() + "Z" if dt else None

        # 2. Check if schedule boundaries are configured
        if open_at or close_at:
            is_after_open = now >= open_at if open_at else True
            is_before_close = now < close_at if close_at else True

            if is_after_open and is_before_close:
                return {
                    "open": True,
                    "reason": "scheduled",
                    "opens_at": to_iso(open_at),
                    "closes_at": to_iso(close_at),
                    "open_at": to_iso(open_at),
                    "close_at": to_iso(close_at)
                }
            else:
                return {
                    "open": False,
                    "reason": "scheduled_closed",
                    "opens_at": to_iso(open_at),
                    "closes_at": to_iso(close_at),
                    "open_at": to_iso(open_at),
                    "close_at": to_iso(close_at)
                }

        # 3. Fallback to manual toggle state if no schedule boundaries are set
        return {
            "open": bool(self.is_open == 1),
            "reason": "manual",
            "opens_at": None,
            "closes_at": None,
            "open_at": None,
            "close_at": None
        }


# ---------------------------------------------------------------------------
# In-memory school cache
# ---------------------------------------------------------------------------

school_databases: dict = {}


def create_school_database(school_name: str, school_code: str):
    if school_name in school_databases:
        return {"status": "error", "message": f"School '{school_name}' database already exists"}
    try:
        school_databases[school_name] = {
            "school_code": school_code,
            "created_at": datetime.utcnow(),
            "active": True,
        }
        return {
            "status": "success",
            "message": f"Database for school '{school_name}' created successfully",
            "school_name": school_name,
            "school_code": school_code,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_school_database_info(school_name: str):
    return school_databases.get(school_name)


def list_all_schools():
    return school_databases


def delete_school_database(school_name: str):
    if school_name not in school_databases:
        return {"status": "error", "message": f"School '{school_name}' not found"}
    try:
        del school_databases[school_name]
        return {"status": "success", "message": f"School '{school_name}' database deleted"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
