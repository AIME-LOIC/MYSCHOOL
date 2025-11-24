# app/crud/teacher_crud.py

from sqlalchemy.orm import Session
from app import models, schemas
from app.utils.security import hash_password


# -------------------------------------------
# CREATE TEACHER
# -------------------------------------------
def create_teacher(db: Session, teacher: schemas.TeacherCreate):
    hashed_password = hash_password(teacher.password)

    new_teacher = models.Teacher(
        full_name=teacher.full_name,
        email=teacher.email,
        password=hashed_password
    )

    db.add(new_teacher)
    db.commit()
    db.refresh(new_teacher)
    return new_teacher


# -------------------------------------------
# GET TEACHER BY ID
# -------------------------------------------
def get_teacher_by_id(db: Session, teacher_id: int):
    return db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()


# -------------------------------------------
# GET TEACHER BY EMAIL (for login)
# -------------------------------------------
def get_teacher_by_email(db: Session, email: str):
    return db.query(models.Teacher).filter(models.Teacher.email == email).first()


# -------------------------------------------
# GET ALL TEACHERS
# -------------------------------------------
def get_all_teachers(db: Session):
    return db.query(models.Teacher).all()


# -------------------------------------------
# UPDATE TEACHER
# -------------------------------------------
def update_teacher(db: Session, teacher_id: int, data: schemas.TeacherUpdate):
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        return None

    if data.full_name:
        teacher.full_name = data.full_name
    
    if data.email:
        teacher.email = data.email

    if data.password:
        teacher.password = hash_password(data.password)

    db.commit()
    db.refresh(teacher)
    return teacher


# -------------------------------------------
# DELETE TEACHER
# -------------------------------------------
def delete_teacher(db: Session, teacher_id: int):
    teacher = get_teacher_by_id(db, teacher_id)
    if not teacher:
        return None

    db.delete(teacher)
    db.commit()
    return True
