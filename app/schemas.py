# app/schemas.py

from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import time


# ---------------------------------------
# TEACHER SCHEMAS
# ---------------------------------------
class TeacherBase(BaseModel):
    full_name: str
    email: EmailStr


class TeacherCreate(TeacherBase):
    password: str


class TeacherUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None


class TeacherResponse(TeacherBase):
    id: int

    class Config:
        from_attributes = True



# ---------------------------------------
# ADMIN SCHEMAS
# ---------------------------------------
class AdminBase(BaseModel):
    full_name: str
    email: EmailStr
    role: str


class AdminCreate(AdminBase):
    password: str


class AdminResponse(AdminBase):
    id: int

    class Config:
        from_attributes = True



# ---------------------------------------
# PARENT SCHEMAS
# ---------------------------------------
class ParentBase(BaseModel):
    full_name: str
    email: EmailStr


class ParentCreate(ParentBase):
    password: str


class ParentResponse(ParentBase):
    id: int

    class Config:
        from_attributes = True



# ---------------------------------------
# CLASS SCHEMAS
# ---------------------------------------
class ClassBase(BaseModel):
    name: str
    class_teacher_id: Optional[int]


class ClassCreate(ClassBase):
    pass


class ClassResponse(ClassBase):
    id: int

    class Config:
        from_attributes = True



# ---------------------------------------
# SUBJECT SCHEMAS
# ---------------------------------------
class SubjectBase(BaseModel):
    name: str
    teacher_id: Optional[int]


class SubjectCreate(SubjectBase):
    pass


class SubjectResponse(SubjectBase):
    id: int

    class Config:
        from_attributes = True



# ---------------------------------------
# STUDENT SCHEMAS
# ---------------------------------------
class StudentBase(BaseModel):
    full_name: str
    email: EmailStr
    parent_id: int
    class_id: Optional[int]


class StudentCreate(StudentBase):
    password: str


class StudentUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    parent_id: Optional[int] = None
    class_id: Optional[int] = None


class StudentResponse(StudentBase):
    id: int

    class Config:
        from_attributes = True



# ---------------------------------------
# TIMETABLE SCHEMAS
# ---------------------------------------
class TimetableBase(BaseModel):
    class_id: int
    subject_id: int
    day_of_week: str
    start_time: time
    end_time: time


class TimetableCreate(TimetableBase):
    pass


class TimetableResponse(TimetableBase):
    id: int

    class Config:
        from_attributes = True
