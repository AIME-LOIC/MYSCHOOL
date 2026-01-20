# models.py
from sqlalchemy import Column, Integer, String, Date, ForeignKey
from sqlalchemy.orm import relationship
from config import Base  # ensure Base = declarative_base() in config.py

class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    student_name = Column(String(100), nullable=False, index=True)
    class_name = Column(String(50), nullable=False)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=True, default=1)  # Made nullable for migration compatibility

    # One-to-many relationship with visits
    visits = relationship(
        "Visit",
        back_populates="student",
        cascade="all, delete"
    )

class Visit(Base):
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)

    visit_type = Column(String(30), nullable=False)  # e.g., 'visit_day', 'parent_meeting'
    visit_date = Column(Date, nullable=False)
    status = Column(String(20), default="waiting")

    # Link back to Student
    student = relationship("Student", back_populates="visits")
