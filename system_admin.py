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
        
        Returns:
            {
              "open": bool,
              "reason": "manual" | "scheduled" | "scheduled_closed",
              "opens_at": datetime | None,   # when it will open (if currently closed)
              "closes_at": datetime | None,  # when it will close (if currently open)
            }
        """
        now = datetime.utcnow()
        has_schedule = self.open_at is not None or self.close_at is not None

        if has_schedule:
            # Both bounds defined → only open inside the window
            if self.open_at and self.close_at:
                if self.open_at <= now <= self.close_at:
                    return {"open": True,  "reason": "scheduled",        "opens_at": None,          "closes_at": self.close_at}
                elif now < self.open_at:
                    return {"open": False, "reason": "scheduled_closed", "opens_at": self.open_at,  "closes_at": None}
                else:  # past close_at
                    return {"open": False, "reason": "scheduled_closed", "opens_at": None,          "closes_at": None}

            # Only open_at defined → open from that point onward
            if self.open_at and not self.close_at:
                if now >= self.open_at:
                    return {"open": True,  "reason": "scheduled",        "opens_at": None,          "closes_at": None}
                else:
                    return {"open": False, "reason": "scheduled_closed", "opens_at": self.open_at,  "closes_at": None}

            # Only close_at defined → open until that point
            if self.close_at and not self.open_at:
                if now <= self.close_at:
                    return {"open": True,  "reason": "scheduled",        "opens_at": None,          "closes_at": self.close_at}
                else:
                    return {"open": False, "reason": "scheduled_closed", "opens_at": None,          "closes_at": None}

        # No schedule → fall back to manual toggle
        return {
            "open": bool(self.is_open),
            "reason": "manual",
            "opens_at": None,
            "closes_at": None,
        }


# ---------------------------------------------------------------------------
# In-memory school cache (unchanged from original)
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