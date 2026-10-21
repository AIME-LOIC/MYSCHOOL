# models.py
from sqlalchemy import Column, Integer, String, Date, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from config import Base
from datetime import datetime

class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    student_name = Column(String(100), nullable=False, index=True)
    class_name = Column(String(50), nullable=False)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=True, default=1)  # Made nullable for migration compatibility
    is_active = Column(Integer, nullable=False, default=1)  # 1 = studying (in active class), 0 = promoted out / archived
    password_hash = Column(String(255), nullable=True)  # Null means the initial zero-padded ID password has not been used yet
    must_change_password = Column(Integer, nullable=False, default=1)
    parent_link_code = Column(String(32), nullable=True, index=True)  # School-issued code for securely linking parent accounts

    # One-to-many relationship with visits
    visits = relationship(
        "Visit",
        back_populates="student",
        cascade="all, delete"
    )

class Product(Base):
    __tablename__ = "products"

    product_id = Column(Integer, primary_key=True, index=True)
    product_name = Column(String(100), nullable=False)
    product_price = Column(Integer, nullable=False)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=True)


class Visit(Base):
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)

    visit_type = Column(String(30), nullable=False)  # e.g., 'visit_day', 'parent_meeting'
    visit_date = Column(Date, nullable=False)
    status = Column(String(20), default="waiting")
    movement_method = Column(String(20), nullable=True)  # with_car | without_car
    arrival_plate_number = Column(String(30), nullable=True)
    parent_name = Column(String(120), nullable=True)
    parent_relationship = Column(String(20), nullable=True)  # father | mother | other
    parent_phone = Column(String(30), nullable=True)
    assigned_plate_number = Column(String(30), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)

    # Link back to Student
    student = relationship("Student", back_populates="visits")


# 1percent Eats: school canteen cards and auditable transactions.
from sqlalchemy import UniqueConstraint

class EatsCard(Base):
    __tablename__ = "eats_cards"
    __table_args__ = (UniqueConstraint("school_id", "uid_hash", name="uq_eats_card_school_uid"),)
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    uid_hash = Column(String(64), nullable=False)
    balance_cents = Column(Integer, nullable=False, default=0)
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class EatsItem(Base):
    __tablename__ = "eats_items"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(String(300), nullable=True)
    price_cents = Column(Integer, nullable=False)
    available = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class EatsTransaction(Base):
    __tablename__ = "eats_transactions"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    card_id = Column(Integer, ForeignKey("eats_cards.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("eats_items.id"), nullable=True)
    transaction_type = Column(String(20), nullable=False, default="purchase")
    amount_cents = Column(Integer, nullable=False)
    balance_after_cents = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="approved")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SupportRequest(Base):
    """Support requests submitted from school portal access/disabled pages."""
    __tablename__ = "support_requests"

    id = Column(Integer, primary_key=True, index=True)
    school_name = Column(String(150), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    email = Column(String(254), nullable=True)
    message = Column(String(3000), nullable=False)
    status = Column(String(20), nullable=False, default="open", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class ParentAccount(Base):
    """Parent portal credentials; each account is linked to one student for this MVP."""
    __tablename__ = "parent_accounts"
    __table_args__ = (UniqueConstraint("school_id", "email", name="uq_parent_school_email"),)
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    parent_name = Column(String(120), nullable=False)
    email = Column(String(254), nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class StudentMark(Base):
    """Published student marks visible to the student's authenticated parent."""
    __tablename__ = "student_marks"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    subject = Column(String(100), nullable=False)
    term = Column(String(40), nullable=False)
    academic_year = Column(String(20), nullable=False)
    score = Column(Integer, nullable=False)
    max_score = Column(Integer, nullable=False, default=100)
    teacher_comment = Column(String(1000), nullable=True)
    published = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CanteenAdmin(Base):
    """Separate canteen staff login scoped to one school."""
    __tablename__ = "canteen_admins"
    __table_args__ = (UniqueConstraint("school_id", "username", name="uq_canteen_admin_school_username"),)
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False, index=True)
    username = Column(String(80), nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
