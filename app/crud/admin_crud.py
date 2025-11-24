# app/crud/admin_crud.py

from sqlalchemy.orm import Session
from app import models, schemas
from app.utils.security import hash_password


# ---------------------------
# CREATE ADMIN
# ---------------------------
def create_admin(db: Session, admin: schemas.AdminCreate):
    hashed_password = hash_password(admin.password)

    new_admin = models.Admin(
        full_name=admin.full_name,
        email=admin.email,
        password=hashed_password,
        role=admin.role
    )

    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    return new_admin


# ---------------------------
# GET ADMIN BY ID
# ---------------------------
def get_admin_by_id(db: Session, admin_id: int):
    return db.query(models.Admin).filter(models.Admin.id == admin_id).first()


# ---------------------------
# GET ADMIN BY EMAIL
# ---------------------------
def get_admin_by_email(db: Session, email: str):
    return db.query(models.Admin).filter(models.Admin.email == email).first()


# ---------------------------
# GET ALL ADMINS
# ---------------------------
def get_all_admins(db: Session):
    return db.query(models.Admin).all()


# ---------------------------
# UPDATE ADMIN
# ---------------------------
def update_admin(db: Session, admin_id: int, data: schemas.AdminCreate):
    admin = get_admin_by_id(db, admin_id)
    if not admin:
        return None

    if data.full_name:
        admin.full_name = data.full_name
    if data.email:
        admin.email = data.email
    if data.password:
        admin.password = hash_password(data.password)
    if data.role:
        admin.role = data.role

    db.commit()
    db.refresh(admin)
    return admin


# ---------------------------
# DELETE ADMIN
# ---------------------------
def delete_admin(db: Session, admin_id: int):
    admin = get_admin_by_id(db, admin_id)
    if not admin:
        return None

    db.delete(admin)
    db.commit()
    return True
