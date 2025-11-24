# app/crud/student_crud.py

from sqlalchemy.orm import Session
from app import models, schemas
from app.utils.security import hash_password


# ---------------------------
# CREATE STUDENT
# ---------------------------
def create_student(db: Session, student: schemas.StudentCreate):
    hashed_password = hash_password(student.password)

    new_student = models.Student(
        full_name=student.full_name,
        email=student.email,
        password=hashed_password,
        parent_id=student.parent_id,
        class_id=student.class_id
    )

    db.add(new_student)
    db.commit()
    db.refresh(new_student)
    return new_student


# ---------------------------
# GET STUDENT BY ID
# ---------------------------
def get_student_by_id(db: Session, student_id: int):
    return db.query(models.Student).filter(models.Student.id == student_id).first()


# ---------------------------
# GET STUDENT BY EMAIL
# ---------------------------
def get_student_by_email(db: Session, email: str):
    return db.query(models.Student).filter(models.Student.email == email).first()


# ---------------------------
# GET ALL STUDENTS
# ---------------------------
def get_all_students(db: Session):
    return db.query(models.Student).all()


# ---------------------------
# UPDATE STUDENT
# ---------------------------
def update_student(db: Session, student_id: int, data: schemas.StudentUpdate):
    student = get_student_by_id(db, student_id)
    if not student:
        return None

    if data.full_name:
        student.full_name = data.full_name
    if data.email:
        student.email = data.email
    if data.password:
        student.password = hash_password(data.password)
    if data.parent_id:
        student.parent_id = data.parent_id
    if data.class_id:
        student.class_id = data.class_id

    db.commit()
    db.refresh(student)
    return student


# ---------------------------
# DELETE STUDENT
# ---------------------------
def delete_student(db: Session, student_id: int):
    student = get_student_by_id(db, student_id)
    if not student:
        return None

    db.delete(student)
    db.commit()
    return True
