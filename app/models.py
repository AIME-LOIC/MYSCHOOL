# app/models.py

from sqlalchemy import Column, Integer, String, Time, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base


# ---------------------------
# TEACHERS TABLE
# ---------------------------
class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)

    # Relationships
    subjects = relationship("Subject", back_populates="teacher")
    classes = relationship("SchoolClass", back_populates="class_teacher")



# ---------------------------
# ADMINS TABLE
# ---------------------------
class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)
    role = Column(String(50), nullable=False)   # DOS, DOD, Director, etc.



# ---------------------------
# PARENTS TABLE
# ---------------------------
class Parent(Base):
    __tablename__ = "parents"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)

    children = relationship("Student", back_populates="parent")



# ---------------------------
# CLASSES TABLE
# ---------------------------
class SchoolClass(Base):  # name changed to avoid Python keyword conflict
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    class_teacher_id = Column(Integer, ForeignKey("teachers.id"))

    class_teacher = relationship("Teacher", back_populates="classes")
    students = relationship("Student", back_populates="class_")
    timetable = relationship("Timetable", back_populates="class_")



# ---------------------------
# SUBJECTS TABLE
# ---------------------------
class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    teacher_id = Column(Integer, ForeignKey("teachers.id"))

    teacher = relationship("Teacher", back_populates="subjects")
    timetable = relationship("Timetable", back_populates="subject")



# ---------------------------
# STUDENTS TABLE
# ---------------------------
class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)

    parent_id = Column(Integer, ForeignKey("parents.id"))
    class_id = Column(Integer, ForeignKey("classes.id"))

    parent = relationship("Parent", back_populates="children")
    class_ = relationship("SchoolClass", back_populates="students")



# ---------------------------
# TIMETABLE TABLE
# ---------------------------
class Timetable(Base):
    __tablename__ = "timetable"

    id = Column(Integer, primary_key=True, index=True)
    class_id = Column(Integer, ForeignKey("classes.id"))
    subject_id = Column(Integer, ForeignKey("subjects.id"))

    day_of_week = Column(String(20), nullable=False)  # Monday, Tuesday, etc.
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    class_ = relationship("SchoolClass", back_populates="timetable")
    subject = relationship("Subject", back_populates="timetable")
