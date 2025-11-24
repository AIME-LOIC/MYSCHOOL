# app/crud/class_crud.py

from sqlalchemy.orm import Session
from app import models, schemas


# ---------------------------
# CREATE CLASS
# ---------------------------
def create_class(db: Session, class_data: schemas.ClassCreate):
    new_class = models.Class(
        name=class_data.name,
        class_teacher_id=class_data.class_teacher_id
    )

    db.add(new_class)
    db.commit()
    db.refresh(new_class)
    return new_class


# ---------------------------
# GET CLASS BY ID
# ---------------------------
def get_class_by_id(db: Session, class_id: int):
    return db.query(models.Class).filter(models.Class.id == class_id).first()


# ---------------------------
# GET ALL CLASSES
# ---------------------------
def get_all_classes(db: Session):
    return db.query(models.Class).all()


# ---------------------------
# UPDATE CLASS
# ---------------------------
def update_class(db: Session, class_id: int, data: schemas.ClassCreate):
    class_obj = get_class_by_id(db, class_id)
    if not class_obj:
        return None

    if data.name:
        class_obj.name = data.name
    if data.class_teacher_id is not None:
        class_obj.class_teacher_id = data.class_teacher_id

    db.commit()
    db.refresh(class_obj)
    return class_obj


# ---------------------------
# DELETE CLASS
# ---------------------------
def delete_class(db: Session, class_id: int):
    class_obj = get_class_by_id(db, class_id)
    if not class_obj:
        return None

    db.delete(class_obj)
    db.commit()
    return True
