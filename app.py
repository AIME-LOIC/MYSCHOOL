from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, Query, HTTPException, Response
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse, HTMLResponse
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Dict
import pandas as pd
import threading, time, requests, os, signal, hashlib, hmac
from datetime import datetime
import re
from openpyxl.styles import Font, PatternFill

from config import get_db, engine, Base, init_db
from model import Student, Visit, Product, SupportRequest
from system_admin import School, create_school_database, list_all_schools, delete_school_database, get_school_database_info
from fastapi.middleware.cors import CORSMiddleware

# Initialize database tables on startup (creates all tables if they don't exist)
init_db()

app = FastAPI(title="School Visit Management System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates & static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Render the branded 404 for browser pages without changing API error behavior."""
    if exc.status_code == 404:
        accepts = request.headers.get("accept", "")
        is_api = "/api/" in request.url.path or "application/json" in accepts
        if not is_api:
            return templates.TemplateResponse(
                "404.html",
                {"request": request},
                status_code=404,
            )
    return await default_http_exception_handler(request, exc)

# ==================== AUTH HELPERS ====================

SESSION_COOKIE = "admin_session"

def _make_token(school_name: str) -> str:
    secret = os.getenv("SECRET_KEY", "myschool_secret_key")
    return hmac.new(secret.encode(), school_name.encode(), hashlib.sha256).hexdigest()

def _check_admin(request: Request, school_name: str) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    return token == _make_token(school_name)


# ─────────────────────────────────────────────────────────────────────────────
# ACCESS GUARD
# ─────────────────────────────────────────────────────────────────────────────

from fastapi.responses import HTMLResponse as _HTMLResponse

def _school_access_guard(school, request):
    status = school.access_status()
    if status["open"]:
        return None  # Portal is open, do not block access
        
    opens_at_iso = status["opens_at"] if status["opens_at"] else ""

    # Ensure this variable assignment aligns exactly with 'opens_at_iso' above
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{school.school_name} — Portal Closed</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{
    font-family:'Segoe UI',sans-serif;
    background:linear-gradient(135deg,#f5f7fa 0%,#c3cfe2 100%);
    min-height:100vh;display:flex;align-items:center;justify-content:center;color:#2d3748;
  }}
  .card{{
    background:#fff;
    box-shadow:0 10px 25px rgba(0,0,0,.08),0 1px 3px rgba(0,0,0,.04);
    border:1px solid #e2e8f0;border-radius:20px;
    padding:50px 40px;max-width:480px;width:90%;text-align:center;
  }}
  .icon{{font-size:64px;margin-bottom:20px}}
  h1{{font-size:26px;font-weight:700;margin-bottom:10px;color:#1a202c}}
  .subtitle{{color:#718096;font-size:15px;margin-bottom:36px;line-height:1.6}}
  .countdown-label{{font-size:13px;text-transform:uppercase;letter-spacing:2px;
    color:#a0aec0;margin-bottom:16px;font-weight:600}}
  .countdown{{display:flex;justify-content:center;gap:16px;margin-bottom:36px}}
  .unit{{background:#edf2f7;border:1px solid #e2e8f0;border-radius:12px;padding:14px 18px;min-width:70px}}
  .unit .num{{font-size:32px;font-weight:700;line-height:1;color:#2b6cb0}}
  .unit .lbl{{font-size:11px;text-transform:uppercase;letter-spacing:1px;
    color:#718096;margin-top:6px;font-weight:500}}
  .no-schedule{{font-size:14px;color:#a0aec0;font-style:italic}}
  .school-badge{{
    display:inline-block;background:#ebf8ff;border:1px solid #bee3f8;
    border-radius:20px;padding:6px 18px;font-size:13px;margin-bottom:28px;
    color:#2b6cb0;font-weight:600;
  }}
  
  /* Support Trigger Panel */
  .support-panel{{
    margin-top:30px;padding-top:24px;
    border-top:1px dashed #e2e8f0;
  }}
  .link-support{{
    color:#2b6cb0;font-size:14px;font-weight:600;
    text-decoration:none;cursor:pointer;
  }}
  .link-support:hover{{text-decoration:underline}}

  /* Popup Modal Overlay */
  .modal-overlay{{
    position:fixed;top:0;left:0;width:100%;height:100%;
    background:rgba(26,32,44,0.4);backdrop-filter:blur(4px);
    display:flex;align-items:center;justify-content:center;
    opacity:0;pointer-events:none;transition:opacity 0.25s ease;z-index:999;
  }}
  .modal-overlay.active{{opacity:1;pointer-events:auto}}

  /* Popup Window Card */
  .modal-card{{
    background:#fff;border-radius:16px;padding:32px;
    width:90%;max-width:440px;box-shadow:0 20px 25px -5px rgba(0,0,0,0.1);
    transform:scale(0.9);transition:transform 0.25s ease;text-align:left;
  }}
  .modal-overlay.active .modal-card{{transform:scale(1)}}

  .modal-title{{font-size:20px;font-weight:700;color:#1a202c;margin-bottom:6px}}
  .modal-subtitle{{font-size:13px;color:#718096;margin-bottom:20px}}
  
  /* Form Styling */
  .form-group{{margin-bottom:16px}}
  .form-group label{{display:block;font-size:13px;font-weight:600;color:#4a5568;margin-bottom:6px}}
  .form-group input, .form-group textarea{{
    width:100%;padding:10px 14px;border:1px solid #cbd5e0;border-radius:8px;
    font-size:14px;color:#2d3748;outline:none;background:#fff;
  }}
  .form-group input:focus, .form-group textarea:focus{{
    border-color:#2b6cb0;box-shadow:0 0 0 3px rgba(43,108,176,0.12);
  }}
  .form-group textarea{{resize:none;height:100px}}
  
  .modal-actions{{display:flex;justify-content:flex-end;gap:12px;margin-top:24px}}
  .btn{{
    padding:10px 16px;font-size:14px;font-weight:600;border-radius:8px;
    cursor:pointer;border:none;transition:background 0.2s;
  }}
  .btn-cancel{{background:#edf2f7;color:#4a5568}}
  .btn-cancel:hover{{background:#e2e8f0}}
  .btn-submit{{background:#2b6cb0;color:#fff}}
  .btn-submit:hover{{background:#2c5282}}
</style>
</head>
<body>

<div id="_d" data-o="{opens_at_iso}" data-s="{school.school_name}" style="display:none"></div>

<div class="card">
  <div class="icon">🔒</div>
  <div class="school-badge">{school.school_name}</div>
  <h1>Sisiteme irafunze</h1>
  <p class="subtitle" id="msg"></p>
  <div id="cw"></div>
  
  <div class="support-panel">
    <span class="link-support" onclick="toggleModal(true)">💬 Saba ubufasha (Ask Support)</span>
  </div>
</div>

<div class="modal-overlay" id="supportModal">
  <div class="modal-card">
    <h2 class="modal-title">Saba Ubufasha</h2>
    <p class="modal-subtitle">Uzuza ubu busabe usake ubufasha bwa sisiteme.</p>
    
    <form id="supportForm" onsubmit="submitSupportRequest(event)">
      <input type="hidden" name="school_name" value="{school.school_name}">

      <div class="form-group">
        <label for="support-name">Amazina yombi / Full Name *</label>
        <input id="support-name" type="text" name="name" required maxlength="120" autocomplete="name" placeholder="e.g., John Doe">
      </div>

      <div class="form-group">
        <label for="support-email">Email / Email address</label>
        <input id="support-email" type="email" name="email" maxlength="254" autocomplete="email" placeholder="you@example.com">
      </div>
      
      <div class="form-group">
        <label for="support-message">Ikibazo ufite / Describe Your Issue *</label>
        <textarea id="support-message" name="message" required maxlength="3000" placeholder="Andika ikibazo cyose ufite hano..."></textarea>
      </div>
      <p id="support-feedback" role="status" aria-live="polite" style="font-size:13px;line-height:1.5;margin-top:8px"></p>
      
      <div class="modal-actions">
        <button type="button" class="btn btn-cancel" onclick="toggleModal(false)">Cancel</button>
        <button id="support-submit" type="submit" class="btn btn-submit">Ohereza (Send)</button>
      </div>
    </form>
  </div>
</div>

<script>
function toggleModal(show) {{
  const modal = document.getElementById('supportModal');
  if (show) {{
    modal.classList.add('active');
  }} else {{
    modal.classList.remove('active');
  }}
}}

async function submitSupportRequest(event) {{
  event.preventDefault();
  const form = document.getElementById('supportForm');
  const feedback = document.getElementById('support-feedback');
  const button = document.getElementById('support-submit');
  const payload = new FormData(form);
  feedback.style.color = '#4a5568';
  feedback.textContent = 'Ohereza birimo gukorwa… / Sending your request…';
  button.disabled = true;
  button.textContent = 'Sending…';
  try {{
    const response = await fetch('/support/requests', {{ method: 'POST', body: payload }});
    const result = await response.json();
    if (!response.ok || result.status !== 'success') {{
      throw new Error(result.message || 'Could not submit your request.');
    }}
    feedback.style.color = '#15803d';
    feedback.textContent = 'Ubusabe bwawe bwoherejwe. / Your support request has been sent.';
    form.reset();
    setTimeout(() => toggleModal(false), 1800);
  }} catch (error) {{
    feedback.style.color = '#b91c1c';
    feedback.textContent = error.message || 'Connection failed. Please try again.';
  }} finally {{
    button.disabled = false;
    button.textContent = 'Ohereza (Send)';
  }}
}}

(function() {{
  var meta    = document.getElementById('_d');
  var opensAt = meta.dataset.o;
  var sname   = meta.dataset.s;
  var msgEl   = document.getElementById('msg');
  var cwEl    = document.getElementById('cw');

  if (!opensAt) {{
    msgEl.textContent = "sisiteme irafunze kugeza kw'Itariki y'umunsi wogusura.";
    cwEl.innerHTML    = "<p class='no-schedule'>Ugaruke nyuma yigihe kirimutsi cg uvugishe ubuyobozi.</p>";
  }} else {{
    msgEl.textContent = "sisiteme iragaruka kumurongo mu masaha yemewe.";
    cwEl.innerHTML =
      "<div class='countdown-label'>Umwanya uburango sisiteme ifunguke</div>" +
      "<div class='countdown'>" +
        "<div class='unit'><div class='num' id='cd-d'>--</div><div class='lbl'>Days</div></div>"   +
        "<div class='unit'><div class='num' id='cd-h'>--</div><div class='lbl'>Hours</div></div>" +
        "<div class='unit'><div class='num' id='cd-m'>--</div><div class='lbl'>Mins</div></div>"   +
        "<div class='unit'><div class='num' id='cd-s'>--</div><div class='lbl'>Secs</div></div>"   +
      "</div>";

    var target = new Date(opensAt);
    var ticker = null;

    function tick() {{
      var diff = target - Date.now();
      if (diff <= 0) {{
        if (ticker) clearInterval(ticker);
        setTimeout(function() {{ location.reload(); }}, 1000);
        return;
      }}
      var dd = Math.floor(diff / 86400000);
      var hh = Math.floor(diff % 86400000 / 3600000);
      var mm = Math.floor(diff % 3600000  / 60000);
      var ss = Math.floor(diff % 60000    / 1000);
      document.getElementById('cd-d').textContent = String(dd).padStart(2, '0');
      document.getElementById('cd-h').textContent = String(hh).padStart(2, '0');
      document.getElementById('cd-m').textContent = String(mm).padStart(2, '0');
      document.getElementById('cd-s').textContent = String(ss).padStart(2, '0');
    }}

    tick();
    ticker = setInterval(tick, 1000);
  }}

  setInterval(function() {{
    fetch('/' + sname + '/portal-status')
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{ if (data.open) location.reload(); }})
      .catch(function() {{}});
  }}, 15000);
}})();
</script>
</body>
</html>"""

    return HTMLResponse(content=html, status_code=503)
   


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM ADMIN — access-control endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/myadmin/schools/{school_id}/toggle")
def toggle_school_access(school_id: int, db: Session = Depends(get_db)):
    """Manually open or close a school portal."""
    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        return {"status": "error", "message": "School not found"}
    school.is_open = 0 if school.is_open else 1
    db.commit()
    return {
        "status": "success",
        "is_open": school.is_open,
        "message": f"Portal {'opened' if school.is_open else 'closed'}",
    }


@app.post("/myadmin/schools/{school_id}/schedule")
def set_school_schedule(
    school_id: int,
    open_at: str | None = Form(None),
    close_at: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Set (or clear) an open/close schedule for a school portal."""
    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        return {"status": "error", "message": "School not found"}

    def _parse(val: str | None) -> datetime | None:
        if not val or not val.strip():
            return None
        
        val_clean = val.strip()
        
        # FIX: Check if the string is an ISO string sent from the frontend (contains 'Z' or 'T')
        if "T" in val_clean:
            try:
                # Handle standard ISO formats and strip timezone information to match your naive DB columns
                dt_aware = datetime.fromisoformat(val_clean.replace("Z", "+00:00"))
                return dt_aware.replace(tzinfo=None)
            except ValueError:
                pass

        # Fallback legacy parsing for other formats
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(val_clean, fmt)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse datetime: {val!r}")

    try:
        school.open_at  = _parse(open_at)
        school.close_at = _parse(close_at)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    db.commit()
    return {
        "status": "success",
        "open_at":  school.open_at.isoformat()  if school.open_at  else None,
        "close_at": school.close_at.isoformat() if school.close_at else None,
        "message": "Schedule updated",
    }


@app.get("/myadmin/schools/{school_id}/access-status")
def get_school_access_status(school_id: int, db: Session = Depends(get_db)):
    """Fetch status for the admin dashboard polling system."""
    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        return {"status": "error", "message": "School not found"}
        
    s = school.access_status()
    
    return {
        "open": s["open"],
        "reason": s["reason"],
        # FIX: These are already strings from access_status(), do not call .isoformat()
        "opens_at":  s["opens_at"],
        "closes_at": s["closes_at"],
        "open_at":   s["open_at"],
        "close_at":  s["close_at"]
    }
@app.get("/{school_name}/portal-status")
def portal_status(school_name: str, db: Session = Depends(get_db)):
    """Lightweight public endpoint polled by the closed page."""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    s = school.access_status()
    return {"open": s["open"]}

# In-memory school cache to avoid repeated DB lookups
_school_cache: dict = {}

def _get_school(school_name: str, db: Session):
    if school_name not in _school_cache:
        school = db.query(School).filter(School.school_name == school_name).first()
        if school:
            _school_cache[school_name] = {"id": school.id, "school_code": school.school_code, "school_name": school.school_name}
    return _school_cache.get(school_name)


@app.post("/support/requests")
def create_support_request(
    school_name: str = Form(...),
    name: str = Form(...),
    message: str = Form(...),
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    """Save a support request from a school portal access-disabled screen."""
    school_name = school_name.strip()[:150]
    name = name.strip()[:120]
    email = email.strip()[:254]
    message = message.strip()[:3000]
    if not school_name or not name or len(message) < 5:
        raise HTTPException(status_code=400, detail="Please provide your name and a clear description of the issue.")
    if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    ticket = SupportRequest(
        school_name=school_name,
        name=name,
        email=email or None,
        message=message,
        status="open",
    )
    try:
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail="Support is temporarily unavailable. Please try again later.")
    return {"status": "success", "ticket_id": ticket.id, "message": "Support request received."}


# ==================== SYSTEM ADMIN ENDPOINTS ====================

@app.get("/myadmin")
def system_admin_dashboard(request: Request):
    """System admin main portal for managing schools"""
    schools = list_all_schools()
    return templates.TemplateResponse("system_admin.html", {
        "request": request,
        "schools": schools,
        "total_schools": len(schools)
    })


@app.post("/myadmin/create-school")
def create_new_school(
    school_name: str = Form(...),
    school_code: str = Form(...),
    db: Session = Depends(get_db)
):
    """Create a new school and its associated database"""
    existing = db.query(School).filter(School.school_name == school_name).first()
    if existing:
        return {"status": "error", "message": "School already exists"}

    school = School(school_name=school_name, school_code=school_code)
    db.add(school)
    db.commit()

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
        db.delete(school)
        db.commit()
        return result


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


@app.delete("/myadmin/schools/{school_id}")
def delete_school(school_id: int, db: Session = Depends(get_db)):
    """Delete a school and its data"""
    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        return {"status": "error", "message": "School not found"}

    db.delete(school)
    db.commit()

    result = delete_school_database(school.school_name)

    return {
        "status": "success",
        "message": f"School '{school.school_name}' deleted successfully"
    }


# ==================== SCHOOL-SPECIFIC ENDPOINTS ====================

# Parent portal — GUARDED
@app.get("/{school_name}/parentportal")
def parent_choice(school_name: str, request: Request, db: Session = Depends(get_db)):
    """Parent portal for a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    # ── Access guard ──
    blocked = _school_access_guard(school, request)
    if blocked:
        return blocked

    return templates.TemplateResponse("index.html", {
        "request": request,
        "school_name": school_name,
        "school_code": school.school_code
    })


# Admin login — NOT guarded (admin must always be able to log in)
@app.get("/{school_name}/admin/login")
def admin_login_page(school_name: str, request: Request, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    if _check_admin(request, school_name):
        return RedirectResponse(f"/{school_name}/admin", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "school_name": school_name, "error": ""})


@app.post("/{school_name}/admin/login")
def admin_login(
    school_name: str,
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    expected_user = school_name
    expected_pass = f"{school_name}001"
    if username == expected_user and password == expected_pass:
        token = _make_token(school_name)
        response = RedirectResponse(f"/{school_name}/admin", status_code=302)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400 * 7)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "school_name": school_name, "error": "Invalid username or password"})


# Admin logout — NOT guarded
@app.get("/{school_name}/admin/logout")
def admin_logout(school_name: str):
    response = RedirectResponse(f"/{school_name}/admin/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


# Admin dashboard — NOT guarded (admin bypass; login check handles auth)
@app.get("/{school_name}/admin")
def admin_choice(school_name: str, request: Request, db: Session = Depends(get_db)):
    """Admin dashboard for a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    if not _check_admin(request, school_name):
        return RedirectResponse(f"/{school_name}/admin/login", status_code=302)
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "school_name": school_name,
        "school_code": school.school_code
    })


# ==================== SCHOOL-SPECIFIC API ENDPOINTS ====================

@app.get("/{school_name}/students/search", response_model=List[Dict])
def search_students(school_name: str, q: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    # Guarded: parents use this endpoint from the parent portal
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    blocked = _school_access_guard(school, Request)
    # Note: For JSON API endpoints we return a JSON error instead of HTML
    status = school.access_status()
    if not status["open"]:
        raise HTTPException(status_code=503, detail="School portal is currently closed")

    query = db.query(Student.id, Student.student_name, Student.class_name).filter(
        (Student.school_id == school.id) | (Student.school_id.is_(None)),
        Student.is_active == 1,  # only students still in school (S3/L5 promoted out are hidden)
        Student.student_name.ilike(f"%{q}%")
    ).limit(20).all()
    return [{"id": s.id, "student_name": s.student_name, "class_name": s.class_name} for s in query]


@app.post("/{school_name}/visits/add")
def add_visit(
    school_name: str,
    student_id: int = Form(...),
    visit_type: str = Form(...),
    movement_method: str | None = Form(None),
    plate_number: str | None = Form(None),
    db: Session = Depends(get_db)
):
    """Add a visit for a student in a specific school"""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    # ── Access guard (JSON response for API endpoint) ──
    status = school.access_status()
    if not status["open"]:
        raise HTTPException(status_code=503, detail="School portal is currently closed")

    try:
        student = db.query(Student).filter(Student.id == student_id).first()
        if not student:
            return {"status": "error", "message": "Student not found"}
        if student.school_id is not None and student.school_id != school.id:
            return {"status": "error", "message": "Student does not belong to this school"}

        if visit_type not in {"visit_day", "parent_meeting"}:
            return {"status": "error", "message": "Invalid visit type"}

        is_sos_school = (
            (school.school_code or "").strip().lower() == "sos"
            or "sos" in (school.school_name or "").strip().lower()
        )

        movement_method_clean = None
        plate_number_clean = None
        if is_sos_school:
            movement_method_clean = (movement_method or "").strip().lower()
            if movement_method_clean not in {"with_car", "without_car"}:
                return {"status": "error", "message": "Please select movement method"}

            if movement_method_clean == "with_car":
                plate_number_clean = (plate_number or "").strip().upper()
                if not plate_number_clean:
                    return {"status": "error", "message": "Plate number is required"}

                plate_number_clean = re.sub(r"\s+", "", plate_number_clean)
                if not re.match(r"^RA[A-Z]\d{3}[A-Z]$", plate_number_clean):
                    return {"status": "error", "message": "Plate number must be in the format RAx 123A (start with RA, then a letter, 3 digits, and a letter)."}
            else:
                plate_number_clean = None

        today = datetime.now().date()

        existing = db.query(Visit).filter(
            Visit.student_id == student.id,
            Visit.visit_type == visit_type,
            Visit.visit_date == today
        ).first()
        if existing:
            return {"status": "error", "message": "Already recorded today"}

        visit = Visit(
            student_id=student.id,
            visit_type=visit_type,
            visit_date=today,
            status="done",
            movement_method=movement_method_clean,
            arrival_plate_number=plate_number_clean,
        )
        db.add(visit)
        db.commit()
        return {"status": "success", "visit_id": visit.id}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}


@app.get("/{school_name}/admin/data/{visit_type}")
def admin_data(school_name: str, visit_type: str, visit_date: str | None = Query(None), db: Session = Depends(get_db)):
    school = _get_school(school_name, db)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    query = db.query(
        Visit.id, Visit.visit_date, Visit.created_at,
        Visit.movement_method, Visit.arrival_plate_number, Visit.assigned_plate_number,
        Student.student_name, Student.class_name
    ).join(Student).filter(
        Visit.visit_type == visit_type,
        (Student.school_id == school["id"]) | (Student.school_id.is_(None))
    )

    if visit_date:
        try:
            target_date = datetime.strptime(visit_date, "%Y-%m-%d").date()
            query = query.filter(Visit.visit_date == target_date)
        except ValueError:
            pass

    rows = query.order_by(Visit.visit_date.desc(), Visit.id.desc()).all()

    result = {}
    for r in rows:
        cls = r.class_name
        if cls not in result:
            result[cls] = []
        result[cls].append({
            "student_name": r.student_name,
            "date": r.visit_date.strftime("%Y-%m-%d"),
            "timestamp": r.created_at.isoformat() if r.created_at else None,
            "id": r.id,
            "movement_method": r.movement_method,
            "arrival_plate_number": r.arrival_plate_number,
            "assigned_plate_number": r.assigned_plate_number,
        })

    stats = {cls: len(students) for cls, students in result.items()}
    return {"data": result, "stats": stats, "total": len(rows)}


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
    invalid = 0
    for _, row in df.iterrows():
        name, cls = row["student_name"], row["class_name"]
        cls = normalize_class_name(cls)
        if not name or not cls:
            invalid += 1
            continue
        if db.query(Student).filter(Student.student_name == str(name).strip(), Student.class_name == cls, Student.school_id == school.id).first():
            skipped += 1
            continue
        db.add(Student(student_name=str(name).strip(), class_name=cls, school_id=school.id))
        added += 1
    db.commit()
    return {"status": "success", "added": added, "skipped": skipped, "invalid": invalid}


@app.get("/{school_name}/admin/students")
def get_all_students(school_name: str, db: Session = Depends(get_db)):
    school = _get_school(school_name, db)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    students = db.query(Student.id, Student.student_name, Student.class_name).filter(
        (Student.school_id == school["id"]) | (Student.school_id.is_(None)),
        Student.is_active == 1  # active students only (archived ones are managed per class)
    ).all()
    return {"status": "success", "students": [{"id": s.id, "student_name": s.student_name, "class_name": s.class_name} for s in students]}


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


@app.delete("/{school_name}/admin/visits/{visit_id}")
def delete_visit(school_name: str, visit_id: int, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    visit = db.query(Visit).join(Student).filter(
        Visit.id == visit_id,
        (Student.school_id == school.id) | (Student.school_id.is_(None))
    ).first()
    if not visit:
        return {"status": "error", "message": "Visit not found"}
    db.delete(visit)
    db.commit()
    return {"status": "success"}


# ==================== CLASS NAME NORMALIZATION ====================

# Canonical class names (no space between level and track): 'L3SOD', 'L3NIT', 'L4SOD',
# 'L4NIT', 'L5SOD', 'L5NIT', 'S1', 'S2', 'S3'. Historical data also contains spaced
# variants ('L3 SOD', 'L4 NIT', ...) which all mean the same class, so every write path
# normalizes class names to the canonical form to keep students in one single class.
VALID_CLASSES = ["S1", "S2", "S3", "L3SOD", "L3NIT", "L4SOD", "L4NIT", "L5SOD", "L5NIT"]
_CLASS_ALIASES = {"S.1": "S1", "S-1": "S1", "S.2": "S2", "S-2": "S2", "S.3": "S3", "S-3": "S3"}

def normalize_class_name(class_name: str) -> str | None:
    """
    Map any class-name variant to its canonical form, or None if unrecognized.
    'l4 nit' -> 'L4NIT', 'L4NIT' -> 'L4NIT', 'L4-NIT' -> 'L4NIT', 's1' -> 'S1'.
    """
    if not class_name:
        return None
    token = re.sub(r"[^A-Za-z0-9]", "", str(class_name)).upper()
    token = _CLASS_ALIASES.get(token, token)
    return token if token in VALID_CLASSES else None


# ==================== STUDENT PROMOTION ====================

# L3/L4/L5 are split into two tracks: SOD and NIT (e.g. 'L3SOD', 'L3NIT').
# Promotion keeps each track together: 'L3SOD' -> 'L4SOD', 'L3NIT' -> 'L4NIT'.
LEVEL_ORDER = ["S1", "S2", "S3", "L3", "L4", "L5"]
NEXT_LEVEL = {"S1": "S2", "S2": "S3", "L3": "L4", "L4": "L5"}
GRADUATED_LEVELS = {"S3", "L5"}  # archived on promotion (data kept, hidden from lists)
LEVEL_RE = re.compile(r"^\s*(S[123]|L[345])(.*)$", re.IGNORECASE)


def _parse_class_level(class_name: str) -> str | None:
    """Extract the level token from a class name: 'L3 SOD' -> 'L3', 'l3sod' -> 'L3'."""
    m = LEVEL_RE.match(class_name or "")
    return m.group(1).upper() if m else None


def _next_class_name(class_name: str) -> str | None:
    """
    Return the promoted class name in canonical form (no space):
    'L3SOD' -> 'L4SOD', 'L3 SOD' -> 'L4SOD', 'l3sod' -> 'L4SOD', 'S1' -> 'S2'.
    Returns None for final-year (S3*/L5*) and unrecognized classes so they are never touched.
    """
    level = _parse_class_level(class_name)
    if level is None or level in GRADUATED_LEVELS:
        return None
    m = LEVEL_RE.match(class_name or "")
    suffix = (m.group(2) or "").replace(" ", "")
    return normalize_class_name(NEXT_LEVEL[level] + suffix)


@app.get("/{school_name}/admin/students/by-class")
def get_students_by_class(
    school_name: str,
    class_name: str | None = Query(None),
    include_archived: bool = Query(False),
    db: Session = Depends(get_db)
):
    """List students per class (not one big list). Pass class_name to get a single class."""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    query = db.query(Student).filter(Student.school_id == school.id)
    if not include_archived:
        query = query.filter(Student.is_active == 1)
    if class_name:
        query = query.filter(Student.class_name == class_name)

    students = query.order_by(Student.class_name.asc(), Student.student_name.asc()).all()

    grouped: dict = {}
    for s in students:
        grouped.setdefault(s.class_name, []).append({
            "id": s.id,
            "student_name": s.student_name,
            "class_name": s.class_name,
            "is_active": bool(s.is_active),
        })

    counts = {cls: len(items) for cls, items in grouped.items()}
    return {"status": "success", "classes": grouped, "counts": counts, "total": len(students)}


def _build_promotion_plan(students) -> tuple:
    """
    Compute the promotion target for each student from a single snapshot.
    L3/L4/L5 tracks (SOD, NIT, ...) move together, e.g. 'L3SOD' -> 'L4SOD'.
    Returns (moves, graduated, skipped) where moves maps 'Old -> New' to a count.
    """
    moves: dict = {}
    graduated: dict = {}
    skipped: dict = {}
    for s in students:
        level = _parse_class_level(s.class_name)
        if level in GRADUATED_LEVELS:
            key = s.class_name
            graduated[key] = graduated.get(key, 0) + 1
            continue
        next_cls = _next_class_name(s.class_name)
        if next_cls is None:
            key = s.class_name or "(no class)"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        key = f"{s.class_name} → {next_cls}"
        moves[key] = moves.get(key, 0) + 1
    return moves, graduated, skipped


def _sort_move_key(move_key: str):
    """Sort moves by level order (S1, S2, L3, L4) then by class name."""
    old_cls = move_key.split("→")[0].strip()
    level = _parse_class_level(old_cls)
    return (LEVEL_ORDER.index(level) if level in LEVEL_ORDER else 99, old_cls)


@app.post("/{school_name}/admin/students/promote/preview")
def promote_preview(school_name: str, db: Session = Depends(get_db)):
    """Show exactly which classes move where (SOD and NIT stay in their tracks) before running."""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    students = db.query(Student).filter(
        Student.school_id == school.id,
        Student.is_active == 1
    ).all()
    moves, graduated, skipped = _build_promotion_plan(students)

    already_done = bool(school.last_promoted_at)
    return {
        "status": "success",
        "moves": [
            {"from": k.split("→")[0].strip(), "to": k.split("→")[1].strip(), "count": v}
            for k, v in sorted(moves.items(), key=lambda kv: _sort_move_key(kv[0]))
        ],
        "graduated": dict(sorted(graduated.items())),
        "skipped": dict(sorted(skipped.items())),
        "already_promoted": already_done,
        "last_promoted_at": school.last_promoted_at.isoformat() if school.last_promoted_at else None,
        "message": "Already promoted this year. New students added after promotion will be affected."
                  if already_done else "Review the numbers then confirm to run the promotion.",
    }


@app.post("/{school_name}/admin/students/promote")
def promote_students(school_name: str, db: Session = Depends(get_db)):
    """
    Promote every active student of this school to the next level, keeping tracks intact:
    S1->S2, S2->S3, and per track 'L3SOD'->'L4SOD', 'L3NIT'->'L4NIT', 'L4SOD'->'L5SOD'.
    Final-year students (S3*, L5*) are archived (is_active = 0) — their data is kept but they
    no longer appear in the active lists. Targets are computed from one snapshot, so no student
    can be promoted twice in the same run.
    """
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    try:
        # Snapshot BEFORE any change so targets are stable (no double promotion)
        students = db.query(Student).filter(
            Student.school_id == school.id,
            Student.is_active == 1
        ).all()

        moves, graduated, skipped = _build_promotion_plan(students)

        promoted_count = 0
        archived_count = 0
        for s in students:
            level = _parse_class_level(s.class_name)
            if level in GRADUATED_LEVELS:
                s.is_active = 0  # archive: data kept, hidden from active lists
                archived_count += 1
                continue
            next_cls = _next_class_name(s.class_name)
            if next_cls is None:
                continue  # unrecognized class names are never touched
            s.class_name = next_cls
            promoted_count += 1

        school.last_promoted_at = datetime.now()
        db.commit()

        return {
            "status": "success",
            "message": "Promotion completed successfully",
            "promoted": promoted_count,
            "graduated": archived_count,
            "moved": moves,
            "graduated_by_class": graduated,
            "skipped": skipped,
        }
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}


@app.put("/{school_name}/admin/students/{student_id}")
def update_student(
    school_name: str,
    student_id: int,
    student_name: str = Form(...),
    class_name: str = Form(...),
    is_active: bool = Form(True),
    db: Session = Depends(get_db)
):
    """Edit a student's name and/or class (e.g. fix wrong class, or restore an archived student)."""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    student = db.query(Student).filter(Student.id == student_id, Student.school_id == school.id).first()
    if not student:
        return {"status": "error", "message": "Student not found"}

    student_name_clean = student_name.strip()
    class_name_clean = normalize_class_name(class_name)
    if not student_name_clean or not class_name_clean:
        return {"status": "error", "message": "Name and a valid class are required"}

    duplicate = db.query(Student).filter(
        Student.student_name == student_name_clean,
        Student.class_name == class_name_clean,
        Student.school_id == school.id,
        Student.id != student_id
    ).first()
    if duplicate:
        return {"status": "error", "message": "Another student with this name already exists in that class"}

    student.student_name = student_name_clean
    student.class_name = class_name_clean
    student.is_active = 1 if is_active else 0
    db.commit()
    return {"status": "success", "message": "Student updated successfully"}


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

    student_name = student_name.strip()
    class_name = normalize_class_name(class_name)
    if not student_name or not class_name:
        return {"status": "error", "message": "Name and a valid class are required"}

    existing = db.query(Student).filter(
        Student.student_name == student_name,
        Student.class_name == class_name,
        Student.school_id == school.id
    ).first()
    if existing:
        return {"status": "error", "message": "Student already exists"}

    student = Student(student_name=student_name, class_name=class_name, school_id=school.id)
    db.add(student)
    db.commit()
    return {"status": "success", "student_id": student.id, "message": "Student added successfully"}


@app.get("/{school_name}/admin/car-management")
def get_car_management_data(
    school_name: str,
    visit_type: str = Query(..., pattern="^(visit_day|parent_meeting)$"),
    visit_date: str | None = Query(None),
    db: Session = Depends(get_db)
):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    is_sos_school = (
        (school.school_code or "").strip().lower() == "sos"
        or "sos" in (school.school_name or "").strip().lower()
    )
    if not is_sos_school:
        return {"status": "error", "message": "Car management is available only for SOS school"}

    target_date = datetime.now().date()
    if visit_date:
        try:
            target_date = datetime.strptime(visit_date, "%Y-%m-%d").date()
        except ValueError:
            return {"status": "error", "message": "Invalid date format. Use YYYY-MM-DD"}

    visits = db.query(Visit).join(Student).filter(
        Visit.visit_type == visit_type,
        Visit.visit_date == target_date,
        (Student.school_id == school.id) | (Student.school_id.is_(None))
    ).order_by(Visit.id.asc()).all()

    return {
        "status": "success",
        "records": [
            {
                "visit_id": v.id,
                "student_id": v.student.id,
                "student_name": v.student.student_name,
                "class_name": v.student.class_name,
                "visit_type": v.visit_type,
                "visit_date": v.visit_date.strftime("%Y-%m-%d"),
                "movement_method": v.movement_method,
                "arrival_plate_number": v.arrival_plate_number,
                "assigned_plate_number": v.assigned_plate_number,
            }
            for v in visits
        ],
    }


@app.post("/{school_name}/admin/car-management/assign")
def assign_car_plate(
    school_name: str,
    visit_id: int = Form(...),
    assigned_plate_number: str = Form(...),
    db: Session = Depends(get_db)
):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    is_sos_school = (
        (school.school_code or "").strip().lower() == "sos"
        or "sos" in (school.school_name or "").strip().lower()
    )
    if not is_sos_school:
        return {"status": "error", "message": "Car management is available only for SOS school"}

    visit = db.query(Visit).join(Student).filter(
        Visit.id == visit_id,
        (Student.school_id == school.id) | (Student.school_id.is_(None))
    ).first()
    if not visit:
        return {"status": "error", "message": "Visit record not found"}

    plate = assigned_plate_number.strip().upper()
    if not plate:
        return {"status": "error", "message": "Assigned plate number is required"}

    visit.assigned_plate_number = plate
    db.commit()
    return {"status": "success", "message": "Car plate assigned successfully", "visit_id": visit.id}


# ==================== PRODUCT ENDPOINTS ====================

@app.get("/{school_name}/products")
def get_products(school_name: str, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    products = db.query(Product).filter(
        (Product.school_id == school.id) | (Product.school_id.is_(None))
    ).all()
    return {"status": "success", "products": [{"product_id": p.product_id, "product_name": p.product_name, "product_price": p.product_price} for p in products]}


@app.post("/{school_name}/admin/products/add")
def add_product(
    school_name: str,
    product_name: str = Form(...),
    product_price: int = Form(...),
    db: Session = Depends(get_db)
):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    product = Product(product_name=product_name, product_price=product_price, school_id=school.id)
    db.add(product)
    db.commit()
    return {"status": "success", "product_id": product.product_id}


@app.put("/{school_name}/admin/products/{product_id}")
def update_product(
    school_name: str,
    product_id: int,
    product_name: str = Form(...),
    product_price: int = Form(...),
    db: Session = Depends(get_db)
):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    product = db.query(Product).filter(Product.product_id == product_id, Product.school_id == school.id).first()
    if not product:
        return {"status": "error", "message": "Product not found"}
    product.product_name = product_name
    product.product_price = product_price
    db.commit()
    return {"status": "success"}


@app.delete("/{school_name}/admin/products/{product_id}")
def delete_product(
    school_name: str,
    product_id: int,
    db: Session = Depends(get_db)
):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    product = db.query(Product).filter(Product.product_id == product_id, Product.school_id == school.id).first()
    if not product:
        return {"status": "error", "message": "Product not found"}
    db.delete(product)
    db.commit()
    return {"status": "success"}


# ==================== EXPORT ENDPOINTS ====================

@app.get("/{school_name}/export/students")
def export_students_to_excel(
    school_name: str,
    class_name: str | None = Query(None),
    db: Session = Depends(get_db)
):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    try:
        query = db.query(Student).filter(
            (Student.school_id == school.id) | (Student.school_id.is_(None))
        )
        class_filter = (class_name or "").strip()
        if class_filter and class_filter.lower() != "all":
            query = query.filter(Student.class_name == class_filter)
        students = query.all()

        data = []
        for student in students:
            data.append({
                "Student Name": student.student_name,
                "Class": student.class_name,
                "ID": student.id
            })

        df = pd.DataFrame(data)
        filename = f"students_{school_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        filepath = f"/tmp/{filename}"

        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Students', index=False)
            workbook = writer.book
            worksheet = writer.sheets['Students']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(cell.value)
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
            header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            header_font = Font(bold=True, color="FFFFFF")
            for cell in worksheet[1]:
                cell.fill = header_fill
                cell.font = header_font

        return FileResponse(filepath, filename=filename, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/{school_name}/export/visits")
def export_visits_to_excel(
    school_name: str,
    visit_type: str = Query(..., pattern="^(visit_day|parent_meeting)$"),
    visit_date: str | None = Query(None),
    db: Session = Depends(get_db)
):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    try:
        query = db.query(Visit).join(Student).filter(
            Visit.visit_type == visit_type,
            (Student.school_id == school.id) | (Student.school_id.is_(None))
        )
        if visit_date:
            try:
                target = datetime.strptime(visit_date, "%Y-%m-%d").date()
                query = query.filter(Visit.visit_date == target)
            except ValueError:
                pass
        visits = query.all()

        data = []
        for visit in visits:
            data.append({
                "Student Name": visit.student.student_name,
                "Class": visit.student.class_name,
                "Visit Type": visit.visit_type.replace('_', ' ').title(),
                "Visit Date": visit.visit_date.strftime("%Y-%m-%d"),
                "Status": visit.status,
                "Movement Method": visit.movement_method if visit.movement_method else "-",
                "Arrival Plate": visit.arrival_plate_number if visit.arrival_plate_number else "-",
                "Assigned Plate": visit.assigned_plate_number if visit.assigned_plate_number else "-",
            })

        df = pd.DataFrame(data)
        visit_type_label = "visit_day" if visit_type == "visit_day" else "parent_meeting"
        filename = f"{visit_type_label}_{school_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        filepath = f"/tmp/{filename}"

        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Visit Data', index=False)
            workbook = writer.book
            worksheet = writer.sheets['Visit Data']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(cell.value)
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
            header_fill = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
            header_font = Font(bold=True, color="FFFFFF")
            for cell in worksheet[1]:
                cell.fill = header_fill
                cell.font = header_font

        return FileResponse(filepath, filename=filename, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ==================== KEEP ALIVE ====================

def keep_awake():
    url = os.environ.get("RENDER_URL", "https://myschool-web-rw.onrender.com")
    while True:
        try:
            requests.get(url)
        except:
            pass
        time.sleep(10 * 60)

threading.Thread(target=keep_awake, daemon=True).start()


# ==================== 1PERCENT EATS ====================
import hashlib as _hashlib
from sqlalchemy import update as _sql_update
from sqlalchemy.exc import IntegrityError as _IntegrityError
from pydantic import BaseModel as _PydanticModel, Field as _Field
from model import EatsCard, EatsItem, EatsTransaction

class EatsCardLink(_PydanticModel):
    student_id: int
    card_uid: str = _Field(min_length=4, max_length=128)

class EatsMenuItemInput(_PydanticModel):
    name: str = _Field(min_length=2, max_length=120)
    description: str = _Field(default="", max_length=300)
    price_rwf: int = _Field(gt=0, le=1000000)

class EatsPaymentInput(_PydanticModel):
    card_uid: str = _Field(min_length=4, max_length=128)
    item_id: int

def _eats_uid_hash(uid: str) -> str:
    pepper = os.getenv("CARD_UID_PEPPER")
    if not pepper:
        raise HTTPException(status_code=503, detail="Canteen card security is not configured (CARD_UID_PEPPER).")
    return _hashlib.sha256((pepper + uid.strip().upper()).encode("utf-8")).hexdigest()

@app.get("/{school_name}/eats")
def eats_page(school_name: str, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    return FileResponse("static/myschool_eats.html")

@app.get("/{school_name}/eats/api/menu")
def eats_menu(school_name: str, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    items = db.query(EatsItem).filter(EatsItem.school_id == school.id, EatsItem.available == 1).order_by(EatsItem.name).all()
    return [{"id": x.id, "name": x.name, "description": x.description or "", "price_rwf": x.price_cents} for x in items]

@app.post("/{school_name}/eats/api/menu")
def eats_add_menu_item(school_name: str, payload: EatsMenuItemInput, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School admin login required")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    item = EatsItem(school_id=school.id, name=payload.name.strip(), description=payload.description.strip(), price_cents=payload.price_rwf)
    db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "name": item.name, "price_rwf": item.price_cents}

@app.post("/{school_name}/eats/api/cards/link")
def eats_link_card(school_name: str, payload: EatsCardLink, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School admin login required")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student = db.query(Student).filter(Student.id == payload.student_id, (Student.school_id == school.id) | (Student.school_id.is_(None))).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found in this school")
    card = EatsCard(school_id=school.id, student_id=student.id, uid_hash=_eats_uid_hash(payload.card_uid), balance_cents=0, active=1)
    db.add(card)
    try:
        db.commit()
    except _IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This card is already linked in this school")
    return {"status": "linked", "student_id": student.id, "card_last4": payload.card_uid.strip()[-4:]}

@app.post("/{school_name}/eats/api/pay")
def eats_pay(school_name: str, payload: EatsPaymentInput, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    uid_hash = _eats_uid_hash(payload.card_uid)
    try:
        card = db.query(EatsCard).filter(EatsCard.school_id == school.id, EatsCard.uid_hash == uid_hash, EatsCard.active == 1).with_for_update().first()
        if not card:
            raise HTTPException(status_code=404, detail="Card is not registered or is inactive")
        item = db.query(EatsItem).filter(EatsItem.id == payload.item_id, EatsItem.school_id == school.id, EatsItem.available == 1).first()
        if not item:
            raise HTTPException(status_code=404, detail="Menu item is unavailable")
        debit = db.execute(_sql_update(EatsCard).where(EatsCard.id == card.id, EatsCard.balance_cents >= item.price_cents).values(balance_cents=EatsCard.balance_cents - item.price_cents))
        if debit.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=402, detail="Insufficient balance")
        db.refresh(card)
        tx = EatsTransaction(school_id=school.id, student_id=card.student_id, card_id=card.id, item_id=item.id, amount_cents=item.price_cents, balance_after_cents=card.balance_cents, status="approved")
        db.add(tx); db.commit(); db.refresh(tx)
        return {"status":"approved", "transaction_id":tx.id, "student_id":card.student_id, "item":item.name, "amount_rwf":tx.amount_cents, "balance_rwf":tx.balance_after_cents}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Payment could not be completed; no approval was returned")

@app.get("/{school_name}/eats/api/transactions")
def eats_transactions(school_name: str, request: Request, db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School admin login required")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    rows = db.query(EatsTransaction).filter(EatsTransaction.school_id == school.id).order_by(EatsTransaction.created_at.desc()).limit(limit).all()
    return [{"id": x.id, "student_id": x.student_id, "item_id": x.item_id, "type": x.transaction_type, "amount_rwf": x.amount_cents, "balance_after_rwf": x.balance_after_cents, "status": x.status, "created_at": x.created_at.isoformat()} for x in rows]


class EatsTopUpInput(_PydanticModel):
    student_id: int
    amount_rwf: int = _Field(gt=0, le=1000000)
    reference: str = _Field(min_length=3, max_length=120)

@app.post("/{school_name}/eats/api/cards/top-up")
def eats_top_up(school_name: str, payload: EatsTopUpInput, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School admin login required")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    card = db.query(EatsCard).filter(EatsCard.school_id == school.id, EatsCard.student_id == payload.student_id, EatsCard.active == 1).with_for_update().first()
    if not card:
        raise HTTPException(status_code=404, detail="No active canteen card found for this student")
    try:
        card.balance_cents += payload.amount_rwf
        tx = EatsTransaction(school_id=school.id, student_id=card.student_id, card_id=card.id, item_id=None, transaction_type="top_up", amount_cents=payload.amount_rwf, balance_after_cents=card.balance_cents, status="approved")
        db.add(tx); db.commit(); db.refresh(tx)
        return {"status":"approved", "transaction_id":tx.id, "student_id":card.student_id, "amount_rwf":payload.amount_rwf, "balance_rwf":card.balance_cents, "reference":payload.reference}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Top-up failed; verify the ledger before retrying")


# ==================== STUDENT PORTAL AUTHENTICATION ====================
import secrets as _secrets
import base64 as _base64

_STUDENT_COOKIE = "myschool_student_session"
_STUDENT_SESSION_SECONDS = 8 * 60 * 60

def _student_password_hash(password: str) -> str:
    salt = _secrets.token_hex(16)
    iterations = 310000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return "pbkdf2_sha256$" + str(iterations) + "$" + salt + "$" + _base64.urlsafe_b64encode(digest).decode("ascii")

def _student_password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(rounds))
        actual = _base64.urlsafe_b64encode(digest).decode("ascii")
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def _student_session_token(student_id: int, school_id: int, expires: int) -> str:
    payload = f"{student_id}:{school_id}:{expires}"
    signature = hmac.new(os.getenv("SECRET_KEY", "myschool_secret_key").encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

def _read_student_session(request: Request, school_id: int):
    token = request.cookies.get(_STUDENT_COOKIE, "")
    try:
        student_id, token_school_id, expires, signature = token.split(":", 3)
        payload = f"{student_id}:{token_school_id}:{expires}"
        expected = hmac.new(os.getenv("SECRET_KEY", "myschool_secret_key").encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or int(token_school_id) != school_id or int(expires) < int(time.time()):
            return None
        return int(student_id)
    except (ValueError, TypeError):
        return None

@app.get("/{school_name}/student-login")
def student_login_page(school_name: str, request: Request, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student_id = _read_student_session(request, school.id)
    student = db.query(Student).filter(Student.id == student_id, Student.school_id == school.id, Student.is_active == 1).first() if student_id else None
    return templates.TemplateResponse("student_portal.html", {"request": request, "school": school, "student": student})

@app.get("/{school_name}/student-login/lookup")
def student_login_lookup(school_name: str, student_id: int = Query(..., ge=1), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student = db.query(Student).filter(Student.id == student_id, Student.school_id == school.id, Student.is_active == 1).first()
    if not student:
        raise HTTPException(status_code=404, detail="No active student with that ID was found in this school.")
    return {"student_id": student.id, "student_name": student.student_name, "class_name": student.class_name}

@app.post("/{school_name}/student-login")
def student_login(school_name: str, request: Request, student_id: int = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student = db.query(Student).filter(Student.id == student_id, Student.school_id == school.id, Student.is_active == 1).first()
    if not student:
        raise HTTPException(status_code=401, detail="Student ID or password is incorrect.")
    if student.password_hash:
        valid = _student_password_matches(password, student.password_hash)
    else:
        valid = hmac.compare_digest(password, str(student.id).zfill(4))
    if not valid:
        raise HTTPException(status_code=401, detail="Student ID or password is incorrect.")
    if not student.password_hash:
        student.password_hash = _student_password_hash(password)
        student.must_change_password = 1
        db.commit()
    expires = int(time.time()) + _STUDENT_SESSION_SECONDS
    token = _student_session_token(student.id, school.id, expires)
    response = JSONResponse({"status": "success", "must_change_password": bool(student.must_change_password)})
    response.set_cookie(_STUDENT_COOKIE, token, max_age=_STUDENT_SESSION_SECONDS, httponly=True, secure=request.url.scheme == "https", samesite="lax", path=f"/{school_name}")
    return response

@app.post("/{school_name}/student/change-password")
def student_change_password(school_name: str, request: Request, new_password: str = Form(...), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student_id = _read_student_session(request, school.id)
    student = db.query(Student).filter(Student.id == student_id, Student.school_id == school.id, Student.is_active == 1).first() if student_id else None
    if not student:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    if len(new_password) < 8 or len(new_password) > 128:
        raise HTTPException(status_code=400, detail="Use a password between 8 and 128 characters.")
    student.password_hash = _student_password_hash(new_password)
    student.must_change_password = 0
    db.commit()
    return {"status": "success"}

@app.post("/{school_name}/student/logout")
def student_logout(school_name: str, request: Request):
    response = JSONResponse({"status": "success"})
    response.delete_cookie(_STUDENT_COOKIE, path=f"/{school_name}")
    return response

@app.get("/{school_name}/student")
def student_dashboard(school_name: str, request: Request, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student_id = _read_student_session(request, school.id)
    student = db.query(Student).filter(Student.id == student_id, Student.school_id == school.id, Student.is_active == 1).first() if student_id else None
    if not student:
        return RedirectResponse(url=f"/{school_name}/student-login", status_code=303)
    return templates.TemplateResponse("student_portal.html", {"request": request, "school": school, "student": student})
