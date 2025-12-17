from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, Query
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Dict
import pandas as pd
import threading, time, requests, os, signal
from datetime import datetime

from config import get_db
from model import Student, Visit
from fastapi.middleware.cors import CORSMiddleware




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

# Parent chooses type
@app.get("/")
def parent_choice(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# Admin dashboard choose type
@app.get("/admin")
def admin_choice(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/students/search", response_model=List[Dict])
def search_students(q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    query = db.query(Student).filter(Student.student_name.ilike(f"%{q}%")).all()
    return [{"id": s.id, "student_name": s.student_name, "class_name": s.class_name} for s in query]
@app.post("/visits/add")
def add_visit(
    student_id: int = Form(...),
    visit_type: str = Form(...),
    db: Session = Depends(get_db)
):
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

@app.get("/admin/data/{visit_type}")
def admin_data(visit_type: str, db: Session = Depends(get_db)):
    """
    visit_type: 'visit_day' or 'parent_meeting'
    Returns all visits of that type grouped by class with stats
    """
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
@app.post("/admin/upload-students")
def upload_students(file: UploadFile = File(...), db: Session = Depends(get_db)):
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
        if db.query(Student).filter(Student.student_name == name, Student.class_name == cls).first():
            skipped += 1
            continue
        db.add(Student(student_name=name, class_name=cls))
        added += 1
    db.commit()
    return {"status": "success", "added": added, "skipped": skipped}
# Get all students
@app.get("/admin/students")
def get_all_students(db: Session = Depends(get_db)):
    students = db.query(Student).all()
    result = [{"id": s.id, "student_name": s.student_name, "class_name": s.class_name} for s in students]
    return {"status": "success", "students": result}


# Delete a student by ID
@app.delete("/admin/students/{student_id}")
def delete_student(student_id: int, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return {"status": "error", "message": "Student not found"}

    db.delete(student)
    db.commit()
    return {"status": "success", "message": f"Student '{student.student_name}' deleted"}
# Add a single student
@app.post("/admin/add_student")
def add_student(
    student_name: str = Form(...),
    class_name: str = Form(...),
    db: Session = Depends(get_db)
):
    # Check if student already exists
    existing = db.query(Student).filter(
        Student.student_name == student_name,
        Student.class_name == class_name
    ).first()
    if existing:
        return {"status": "error", "message": "Student already exists"}

    # Add new student
    student = Student(student_name=student_name, class_name=class_name)
    db.add(student)
    db.commit()
    return {"status": "success", "student_id": student.id, "message": "Student added successfully"}

import threading
import time
import requests
import os

def keep_awake():
    url = os.environ.get("https://myschool-rw-web.onrender.com")  # set your deployed URL in env variables
    while True:
        try:
            requests.get(url)
        except:
            pass
        time.sleep(10 * 60)  # ping every 10 minutes

threading.Thread(target=keep_awake, daemon=True).start()
