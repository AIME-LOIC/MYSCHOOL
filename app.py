from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, Query, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Dict
import pandas as pd
import threading, time, requests, os, signal
from datetime import datetime

from config import get_db, engine, Base, init_db
from model import Student, Visit
from system_admin import School, create_school_database, list_all_schools, delete_school_database, get_school_database_info
from fastapi.middleware.cors import CORSMiddleware

# Initialize database tables on startup (creates all tables if they don't exist)
init_db()

app = FastAPI(title="School Visit Management System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates & static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ==================== SYSTEM ADMIN ENDPOINTS ====================

# System Admin - Main dashboard
@app.get("/myadmin")
def system_admin_dashboard(request: Request):
    """System admin main portal for managing schools"""
    schools = list_all_schools()
    return templates.TemplateResponse("system_admin.html", {
        "request": request,
        "schools": schools,
        "total_schools": len(schools)
    })


# System Admin - Create new school
@app.post("/myadmin/create-school")
def create_new_school(
    school_name: str = Form(...),
    school_code: str = Form(...),
    db: Session = Depends(get_db)
):
    """Create a new school and its associated database"""
    # Check if school already exists
    existing = db.query(School).filter(School.school_name == school_name).first()
    if existing:
        return {"status": "error", "message": "School already exists"}
    
    # Create school in system database
    school = School(school_name=school_name, school_code=school_code)
    db.add(school)
    db.commit()
    
    # Create school-specific database
    result = create_school_database(school_name, school_code)
    
    if result["status"] == "success":
        return {
            "status": "success",
            "message": f"School '{school_name}' created successfully",
            "school_id": school.id,
            "school_name": school_name,
            "school_code": school_code
        }
    else:
        # Rollback if database creation fails
        db.delete(school)
        db.commit()
        return result


# System Admin - List all schools
@app.get("/myadmin/schools")
def get_all_schools(db: Session = Depends(get_db)):
    """Get list of all registered schools"""
    schools = db.query(School).filter(School.is_active == 1).all()
    return {
        "status": "success",
        "total": len(schools),
        "schools": [
            {
                "id": s.id,
                "school_name": s.school_name,
                "school_code": s.school_code,
                "created_at": s.created_at,
                "is_active": s.is_active
            } for s in schools
        ]
    }


# System Admin - Delete school
@app.delete("/myadmin/schools/{school_id}")
def delete_school(school_id: int, db: Session = Depends(get_db)):
    """Delete a school and its data"""
    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        return {"status": "error", "message": "School not found"}
    
    # Delete from database
    db.delete(school)
    db.commit()
    
    # Delete school-specific database
    result = delete_school_database(school.school_name)
    
    return {
        "status": "success",
        "message": f"School '{school.school_name}' deleted successfully"
    }


# ==================== SCHOOL-SPECIFIC ENDPOINTS ====================

# Parent chooses type for specific school
@app.get("/{school_name}/parentportal")
def parent_choice(school_name: str, request: Request, db: Session = Depends(get_db)):
    """Parent portal for a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "school_name": school_name,
        "school_code": school.school_code
    })


# Admin dashboard for specific school
@app.get("/{school_name}/admin")
def admin_choice(school_name: str, request: Request, db: Session = Depends(get_db)):
    """Admin dashboard for a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "school_name": school_name,
        "school_code": school.school_code
    })


# ==================== SCHOOL-SPECIFIC API ENDPOINTS ====================

@app.get("/{school_name}/students/search", response_model=List[Dict])
def search_students(school_name: str, q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """Search students in a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    # Search for students with matching school_id (or students with no school assigned)
    query = db.query(Student).filter(
        (Student.school_id == school.id) | (Student.school_id.is_(None)),
        Student.student_name.ilike(f"%{q}%")
    ).all()
    return [{"id": s.id, "student_name": s.student_name, "class_name": s.class_name} for s in query]


@app.post("/{school_name}/visits/add")
def add_visit(
    school_name: str,
    student_id: int = Form(...),
    visit_type: str = Form(...),
    db: Session = Depends(get_db)
):
    """Add a visit for a student in a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    try:
        student = db.query(Student).filter(Student.id == student_id).first()
        if not student:
            return {"status": "error", "message": "Student not found"}

        today = datetime.now().date()

        existing = db.query(Visit).filter(
            Visit.student_id == student.id,
            Visit.visit_type == visit_type,
            Visit.visit_date == today
        ).first()
        if existing:
            return {"status": "error", "message": "Already recorded today"}

        visit = Visit(student_id=student.id, visit_type=visit_type, visit_date=today, status="done")
        db.add(visit)
        db.commit()
        return {"status": "success", "visit_id": visit.id}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}


@app.get("/{school_name}/admin/data/{visit_type}")
def admin_data(school_name: str, visit_type: str, db: Session = Depends(get_db)):
    """
    Get visit data for a specific school
    visit_type: 'visit_day' or 'parent_meeting'
    Returns all visits of that type grouped by class with stats
    """
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    visits = db.query(Visit).join(Student).filter(Visit.visit_type == visit_type).all()

    # Prepare structured data
    result = {}
    for v in visits:
        cls = v.student.class_name
        if cls not in result:
            result[cls] = []
        result[cls].append({"student_name": v.student.student_name, "date": v.visit_date.strftime("%Y-%m-%d")})

    # Stats
    stats = {cls: len(students) for cls, students in result.items()}

    return {"data": result, "stats": stats, "total": len(visits)}


@app.post("/{school_name}/admin/upload-students")
def upload_students(school_name: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload students for a specific school from Excel file"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    try:
        df = pd.read_excel(file.file)
    except Exception:
        return JSONResponse({"error": "Invalid Excel file"}, status_code=400)

    required_columns = {"student_name", "class_name"}
    if not required_columns.issubset(df.columns):
        return JSONResponse({"error": "Invalid Excel format"}, status_code=400)

    added = 0
    skipped = 0
    for _, row in df.iterrows():
        name, cls = row["student_name"], row["class_name"]
        if db.query(Student).filter(Student.student_name == name, Student.class_name == cls, Student.school_id == school.id).first():
            skipped += 1
            continue
        db.add(Student(student_name=name, class_name=cls, school_id=school.id))
        added += 1
    db.commit()
    return {"status": "success", "added": added, "skipped": skipped}


# Get all students for a specific school
@app.get("/{school_name}/admin/students")
def get_all_students(school_name: str, db: Session = Depends(get_db)):
    """Get all students for a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    students = db.query(Student).filter(
        (Student.school_id == school.id) | (Student.school_id.is_(None))
    ).all()
    result = [{"id": s.id, "student_name": s.student_name, "class_name": s.class_name} for s in students]
    return {"status": "success", "students": result}


# Delete a student by ID from a specific school
@app.delete("/{school_name}/admin/students/{student_id}")
def delete_student(school_name: str, student_id: int, db: Session = Depends(get_db)):
    """Delete a student from a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return {"status": "error", "message": "Student not found"}

    db.delete(student)
    db.commit()
    return {"status": "success", "message": f"Student '{student.student_name}' deleted"}


# Add a single student to a specific school
@app.post("/{school_name}/admin/add_student")
def add_student(
    school_name: str,
    student_name: str = Form(...),
    class_name: str = Form(...),
    db: Session = Depends(get_db)
):
    """Add a single student to a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    # Check if student already exists
    existing = db.query(Student).filter(
        Student.student_name == student_name,
        Student.class_name == class_name,
        Student.school_id == school.id
    ).first()
    if existing:
        return {"status": "error", "message": "Student already exists"}

    # Add new student with school_id
    student = Student(student_name=student_name, class_name=class_name, school_id=school.id)
    db.add(student)
    db.commit()
    return {"status": "success", "student_id": student.id, "message": "Student added successfully"}

import threading
import time
import requests
import os

def keep_awake():
    url = os.environ.get("RENDER_URL", "https://myschool-rw-web.onrender.com")  # set your deployed URL in env variables
    while True:
        try:
            requests.get(url)
        except:
            pass
        time.sleep(10 * 60)  # ping every 10 minutes

threading.Thread(target=keep_awake, daemon=True).start()
