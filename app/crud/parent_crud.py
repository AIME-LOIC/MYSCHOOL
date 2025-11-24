# app/crud/parent_crud.py

from sqlalchemy.orm import Session
from app import models, schemas
from app.utils.security import hash_password


# ---------------------------
# CREATE PARENT
# ---------------------------
def create_parent(db: Session, parent: schemas.ParentCreate):
    hashed_password = hash_password(parent.password)

    new_parent = models.Parent(
        full_name=parent.full_name,
        email=parent.email,
        password=hashed_password
    )

    db.add(new_parent)
    db.commit()
    db.refresh(new_parent)
    return new_parent


# ---------------------------
# GET PARENT BY ID
# ---------------------------
def get_parent_by_id(db: Session, parent_id: int):
    return db.query(models.Parent).filter(models.Parent.id == parent_id).first()


# ---------------------------
# GET PARENT BY EMAIL
# ---------------------------
def get_parent_by_email(db: Session, email: str):
    return db.query(models.Parent).filter(models.Parent.email == email).first()


# ---------------------------
# GET ALL PARENTS
# ---------------------------
def get_all_parents(db: Session):
    return db.query(models.Parent).all()


# ---------------------------
# UPDATE PARENT
# ---------------------------
def update_parent(db: Session, parent_id: int, data: schemas.ParentCreate):
    parent = get_parent_by_id(db, parent_id)
    if not parent:
        return None

    if data.full_name:
        parent.full_name = data.full_name
    if data.email:
        parent.email = data.email
    if data.password:
        parent.password = hash_password(data.password)

    db.commit()
    db.refresh(parent)
    return parent


# ---------------------------
# DELETE PARENT
# ---------------------------
def delete_parent(db: Session, parent_id: int):
    parent = get_parent_by_id(db, parent_id)
    if not parent:
        return None

    db.delete(parent)
    db.commit()
    return True
