from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, Query, HTTPException, Response
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse, HTMLResponse
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from pydantic import BaseModel
import pandas as pd
import threading, time, requests, os, signal, hashlib, hmac
from datetime import datetime
import re
import json
import logging
from difflib import SequenceMatcher
from openpyxl.styles import Font, PatternFill

from config import get_db, engine, Base, init_db
from model import Student, Visit, Product, SupportRequest, ParentAccount, StudentMark, CanteenAdmin, Teacher, TimetableSlot
from system_admin import School, create_school_database, list_all_schools, delete_school_database, get_school_database_info
from fastapi.middleware.cors import CORSMiddleware

# Initialize database tables on startup (creates all tables if they don't exist)
init_db()

logger = logging.getLogger("myschool.visits")

app = FastAPI(title="School Visit Management System")
# Cross-origin access is opt-in. Same-origin browser requests do not require CORS.
# Set CORS_ALLOWED_ORIGINS to a comma-separated list only when a separate trusted
# frontend must call this API. Never combine credentialed requests with a wildcard.
CORS_ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

# Templates & static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _typo_redirect_target(request: Request) -> str | None:
    """Find a safe, unambiguous URL correction for a mistyped browser path."""
    path = request.url.path
    if path.startswith(("/api/", "/static/")) or path in {"/docs", "/redoc", "/openapi.json"}:
        return None

    parts = path.strip("/").split("/") if path != "/" else []
    if not parts:
        return None

    candidates: list[tuple[float, str]] = []
    for route in app.routes:
        route_path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not route_path.startswith("/") or not ({"GET", "HEAD"} & methods):
            continue
        if route_path.startswith(("/api/", "/static/")) or route_path in {"/docs", "/redoc", "/openapi.json"}:
            continue

        route_parts = route_path.strip("/").split("/") if route_path != "/" else []
        if len(parts) != len(route_parts):
            continue

        corrected = []
        changed = 0
        score = 1.0
        valid = True
        for actual, expected in zip(parts, route_parts):
            if expected.startswith("{") and expected.endswith("}"):
                # Keep dynamic values (such as a school name) exactly as entered.
                corrected.append(actual)
                continue
            if actual == expected:
                corrected.append(expected)
                continue

            # Only suggest clear, one-segment spelling corrections.
            if len(actual) < 4 or len(expected) < 4:
                valid = False
                break
            similarity = SequenceMatcher(None, actual.lower(), expected.lower()).ratio()
            if similarity < 0.78:
                valid = False
                break
            changed += 1
            score = min(score, similarity)
            corrected.append(expected)

        if valid and changed == 1:
            candidates.append((score, "/" + "/".join(corrected)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    # Do not guess when two routes are similarly plausible.
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.04:
        return None
    return candidates[0][1]


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Correct obvious browser URL typos and render the branded 404 otherwise."""
    if exc.status_code == 404:
        accepts = request.headers.get("accept", "")
        is_api = "/api/" in request.url.path or "application/json" in accepts
        if not is_api and request.method in {"GET", "HEAD"}:
            corrected_path = _typo_redirect_target(request)
            if corrected_path and corrected_path != request.url.path:
                return RedirectResponse(
                    url=f"{corrected_path}?{request.url.query}" if request.url.query else corrected_path,
                    status_code=307,
                )
        if not is_api:
            return templates.TemplateResponse(
                "404.html",
                {"request": request},
                status_code=404,
            )
    return await default_http_exception_handler(request, exc)

# ==================== AUTH HELPERS ====================

SESSION_COOKIE = "admin_session"
_ADMIN_SESSION_SECONDS = 86400 * 7

def _signing_secret() -> str:
    """Return the required signing key; never silently sign sessions with a public default."""
    secret = os.getenv("SECRET_KEY", "").strip()
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured. Set SECRET_KEY to a random value of at least 32 characters.",
        )
    return secret

def _admin_secret_configured() -> bool:
    """Require a deployment-provided signing secret; never use a known fallback."""
    return len(os.getenv("SECRET_KEY", "").strip()) >= 32

def _admin_password_for_school(school_name: str) -> str | None:
    """Read a unique administrator password for this school from deployment config."""
    try:
        configured = json.loads(os.getenv("MYSCHOOL_ADMIN_PASSWORDS", "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(configured, dict):
        return None
    password = configured.get(school_name)
    return password if isinstance(password, str) and len(password) >= 12 else None

def _make_token(school_name: str) -> str:
    secret = os.getenv("SECRET_KEY", "").strip()
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="Admin authentication is not configured. Set SECRET_KEY to a random value of at least 32 characters.",
        )
    expires = int(time.time()) + _ADMIN_SESSION_SECONDS
    message = f"{school_name}:{expires}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"

def _check_admin(request: Request, school_name: str) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or not _admin_secret_configured():
        return False
    try:
        expiry_text, supplied_signature = token.split(".", 1)
        expires = int(expiry_text)
    except (ValueError, TypeError):
        return False
    if expires <= int(time.time()) or expires > int(time.time()) + _ADMIN_SESSION_SECONDS + 60:
        return False
    message = f"{school_name}:{expires}"
    expected_signature = hmac.new(
        os.getenv("SECRET_KEY", "").strip().encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(supplied_signature, expected_signature)


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

def _is_impactful_school(school: School) -> bool:
    """Identify Impactful Technical High School Kigali, retaining its legacy SOS code."""
    school_name = (getattr(school, "school_name", "") or "").strip().lower()
    school_code = (getattr(school, "school_code", "") or "").strip().lower()
    return "impactful technical high school" in school_name or school_code == "sos"


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



@app.get("/", response_class=HTMLResponse)
def school_selector(request: Request):
    """Public home page: show the school selector instead of a blank or missing route."""
    return templates.TemplateResponse("school_select.html", {"request": request})


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

@app.get("/{school_name}/portals", response_class=HTMLResponse)
def school_portal_hub(school_name: str, request: Request, db: Session = Depends(get_db)):
    """Role-based portal directory for a school."""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    return templates.TemplateResponse("role_hub.html", {
        "request": request,
        "school": school,
        "school_name": school_name,
    })


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
    expected_pass = _admin_password_for_school(school_name)
    if not _admin_secret_configured() or expected_pass is None:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "school_name": school_name,
                "error": "Administrator authentication is not configured for this school. Please contact the system administrator.",
            },
            status_code=503,
        )
    expected_user = school_name
    if username == expected_user and hmac.compare_digest(password, expected_pass):
        token = _make_token(school_name)
        response = RedirectResponse(f"/{school_name}/admin", status_code=302)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, secure=request.url.scheme == "https",
            samesite="lax", max_age=_ADMIN_SESSION_SECONDS, path="/",
        )
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


@app.post("/{school_name}/parent-meeting/unlock")
def unlock_parent_meeting(school_name: str, request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    """Validate the parent-meeting code and issue a short-lived, signed HttpOnly cookie."""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    if not re.fullmatch(r"[0-9]{4}", (code or "").strip()):
        raise HTTPException(status_code=403, detail="Enter a valid four-digit code.")
    # Override this default with PARENT_MEETING_ACCESS_CODE in the production environment.
    expected_code = os.getenv("PARENT_MEETING_ACCESS_CODE", "1324").strip()
    if not hmac.compare_digest(code.strip(), expected_code):
        raise HTTPException(status_code=403, detail="Incorrect code. Please try again.")
    expires = int(time.time()) + 60 * 60
    payload = f"parent-meeting:{school_name}:{expires}"
    signature = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{payload}:{signature}"
    response = JSONResponse({"status": "success", "valid_for_minutes": 60})
    response.set_cookie(
        "myschool_parent_meeting_access", token, max_age=60 * 60,
        httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/"
    )
    return response


def _has_parent_meeting_access(request: Request, school_name: str) -> bool:
    token = request.cookies.get("myschool_parent_meeting_access", "")
    try:
        role, school, expires, signature = token.split(":", 3)
        payload = f"{role}:{school}:{expires}"
        expected = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        return (
            role == "parent-meeting" and school == school_name
            and hmac.compare_digest(signature, expected)
            and int(expires) >= int(time.time())
        )
    except (ValueError, TypeError, HTTPException):
        return False


@app.post("/{school_name}/visits/add")
def add_visit(
    school_name: str,
    request: Request,
    student_id: int = Form(...),
    visit_type: str = Form(...),
    movement_method: str | None = Form(None),
    plate_number: str | None = Form(None),
    parent_name: str | None = Form(None),
    parent_relationship: str | None = Form(None),
    parent_phone: str | None = Form(None),
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

        if visit_type == "parent_meeting" and not _has_parent_meeting_access(request, school_name):
            raise HTTPException(status_code=403, detail="Enter the parent-meeting access code before submitting this meeting.")

        is_impactful_school = _is_impactful_school(school)

        movement_method_clean = None
        plate_number_clean = None
        if is_impactful_school:
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

        parent_name_clean = (parent_name or "").strip()[:120] or None
        relationship_clean = (parent_relationship or "").strip().lower() or None
        parent_phone_clean = (parent_phone or "").strip()[:30] or None
        if visit_type == "parent_meeting":
            if not parent_name_clean:
                return {"status": "error", "message": "Parent name is required for a parent meeting"}
            if relationship_clean not in {"father", "mother", "brother", "sister", "other"}:
                return {"status": "error", "message": "Select father, mother, brother, sister, or other"}
            if not parent_phone_clean:
                return {"status": "error", "message": "Phone number is required for a parent meeting"}
        else:
            parent_name_clean = relationship_clean = parent_phone_clean = None

        visit = Visit(
            student_id=student.id,
            visit_type=visit_type,
            parent_name=parent_name_clean,
            parent_relationship=relationship_clean,
            parent_phone=parent_phone_clean,
            visit_date=today,
            status="done",
            movement_method=movement_method_clean,
            arrival_plate_number=plate_number_clean,
        )
        db.add(visit)
        db.commit()
        return {"status": "success", "visit_id": visit.id}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        error_id = hashlib.sha256(f"{school_name}:{student_id}:{visit_type}:{time.time_ns()}".encode()).hexdigest()[:12]
        logger.exception(
            "Visit submission failed [error_id=%s school=%s student_id=%s visit_type=%s]",
            error_id, school_name, student_id, visit_type
        )
        message = "Database error while saving this visit. Please contact the administrator and provide error ID " + error_id
        return {"status": "error", "message": message, "error_id": error_id}


@app.get("/{school_name}/admin/data/{visit_type}")
def admin_data(school_name: str, visit_type: str, visit_date: str | None = Query(None), db: Session = Depends(get_db)):
    school = _get_school(school_name, db)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")

    query = db.query(
        Visit.id, Visit.visit_date, Visit.created_at,
        Visit.movement_method, Visit.arrival_plate_number, Visit.assigned_plate_number,
        Visit.parent_name, Visit.parent_relationship, Visit.parent_phone,
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
            "parent_name": r.parent_name,
            "parent_relationship": r.parent_relationship,
            "parent_phone": r.parent_phone,
            "class_name": r.class_name,
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
    student.canteen_card_id = _new_student_canteen_card_id(db, school.id)
    db.add(student)
    db.flush()
    _ensure_student_eats_card(db, school.id, student.id, student.canteen_card_id)
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
    is_impactful_school = _is_impactful_school(school)
    if not is_impactful_school:
        return {"status": "error", "message": "Car management is available only for Impactful Technical High School Kigali"}

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
    is_impactful_school = _is_impactful_school(school)
    if not is_impactful_school:
        return {"status": "error", "message": "Car management is available only for Impactful Technical High School Kigali"}

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
def export_visits_to_excel(school_name: str, visit_type: str = Query(..., pattern="^(visit_day|parent_meeting)$"), visit_date: str | None = Query(None), class_name: str | None = Query(None), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    try:
        query = db.query(Visit).join(Student).filter(Visit.visit_type == visit_type, (Student.school_id == school.id) | (Student.school_id.is_(None)))
        if visit_date:
            try:
                query = query.filter(Visit.visit_date == datetime.strptime(visit_date, "%Y-%m-%d").date())
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid visit date. Use YYYY-MM-DD.")
        if class_name and class_name.strip() and class_name.strip().lower() != "all":
            query = query.filter(Student.class_name == class_name.strip())
        visits = query.order_by(Visit.visit_date.desc(), Visit.id.desc()).all()
        columns = ["Student Name", "Class", "Visit Type", "Visit Date", "Recorded At", "Parent / Guardian Name", "Relationship", "Parent Phone", "Status", "Movement Method", "Arrival Plate", "Assigned Plate", "Record ID"]
        data = [{
            "Student Name": v.student.student_name, "Class": v.student.class_name,
            "Visit Type": v.visit_type.replace("_", " ").title(),
            "Visit Date": v.visit_date.strftime("%Y-%m-%d") if v.visit_date else "",
            "Recorded At": v.created_at.strftime("%Y-%m-%d %H:%M:%S") if v.created_at else "",
            "Parent / Guardian Name": v.parent_name or "", "Relationship": (v.parent_relationship or "").title(),
            "Parent Phone": v.parent_phone or "", "Status": v.status or "",
            "Movement Method": v.movement_method or "", "Arrival Plate": v.arrival_plate_number or "",
            "Assigned Plate": v.assigned_plate_number or "", "Record ID": v.id
        } for v in visits]
        df = pd.DataFrame(data, columns=columns)
        kind = "visit_day" if visit_type == "visit_day" else "parent_meeting"
        scope = class_name.strip().replace(" ", "_") if class_name and class_name.strip().lower() != "all" else "full_school"
        filename = f"{kind}_{scope}_{school_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        filepath = f"/tmp/{filename}"
        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Attendance", index=False)
            ws = writer.sheets["Attendance"]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for column in ws.columns:
                max_length = max((len(str(cell.value or "")) for cell in column), default=0)
                ws.column_dimensions[column[0].column_letter].width = min(max_length + 2, 42)
            for cell in ws[1]:
                cell.fill = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
                cell.font = Font(bold=True, color="FFFFFF")
        return FileResponse(filepath, filename=filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Visit Excel export failed for school=%s type=%s class=%s", school_name, visit_type, class_name)
        return JSONResponse({"error": "Could not export attendance. Please try again."}, status_code=500)


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
    if not (_check_admin(request, school_name) or _read_canteen_session(request, school_name)):
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
    uid_hash = _eats_uid_hash(payload.card_uid)
    linked_uid = db.query(EatsCard).filter(EatsCard.school_id == school.id, EatsCard.uid_hash == uid_hash).first()
    if linked_uid and linked_uid.student_id != student.id:
        raise HTTPException(status_code=409, detail="This NFC card is already linked to another student in this school")
    # Reuse the student's existing account so manual-ID and NFC purchases share one balance.
    card = db.query(EatsCard).filter(EatsCard.school_id == school.id, EatsCard.student_id == student.id).first()
    if card:
        card.uid_hash = uid_hash
        card.active = 1
    else:
        card = EatsCard(school_id=school.id, student_id=student.id, uid_hash=uid_hash, balance_cents=0, active=1)
        db.add(card)
    try:
        db.commit()
    except _IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This NFC card is already linked in this school")
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
    signature = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

def _read_student_session(request: Request, school_id: int):
    token = request.cookies.get(_STUDENT_COOKIE, "")
    try:
        student_id, token_school_id, expires, signature = token.split(":", 3)
        payload = f"{student_id}:{token_school_id}:{expires}"
        expected = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
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
    student = db.query(Student).filter(Student.id == student_id, ((Student.school_id == school.id) | (Student.school_id.is_(None))), Student.is_active == 1).first() if student_id else None
    return templates.TemplateResponse("student_portal.html", {"request": request, "school": school, "student": student})

@app.get("/{school_name}/student-login/lookup")
def student_login_lookup(school_name: str, student_id: int = Query(..., ge=1), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student = db.query(Student).filter(Student.id == student_id, ((Student.school_id == school.id) | (Student.school_id.is_(None))), Student.is_active == 1).first()
    if not student:
        raise HTTPException(status_code=404, detail="No active student with that ID was found in this school.")
    return {"student_id": student.id, "student_name": student.student_name, "class_name": student.class_name}

@app.post("/{school_name}/student-login")
def student_login(school_name: str, request: Request, student_id: int = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student = db.query(Student).filter(Student.id == student_id, ((Student.school_id == school.id) | (Student.school_id.is_(None))), Student.is_active == 1).first()
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
    response.set_cookie(_STUDENT_COOKIE, token, max_age=_STUDENT_SESSION_SECONDS, httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return response

@app.post("/{school_name}/student/change-password")
def student_change_password(school_name: str, request: Request, new_password: str = Form(...), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student_id = _read_student_session(request, school.id)
    student = db.query(Student).filter(Student.id == student_id, ((Student.school_id == school.id) | (Student.school_id.is_(None))), Student.is_active == 1).first() if student_id else None
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
    response.delete_cookie(_STUDENT_COOKIE, path="/")
    return response

@app.get("/{school_name}/student")
def student_dashboard(school_name: str, request: Request, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student_id = _read_student_session(request, school.id)
    student = db.query(Student).filter(Student.id == student_id, ((Student.school_id == school.id) | (Student.school_id.is_(None))), Student.is_active == 1).first() if student_id else None
    if not student:
        return RedirectResponse(url=f"/{school_name}/student-login", status_code=303)
    return templates.TemplateResponse("student_portal.html", {"request": request, "school": school, "student": student})


# ==================== PARENT ACCOUNTS AND REPORTS ====================
_PARENT_COOKIE = "myschool_parent_session"
_PARENT_SESSION_SECONDS = 8 * 60 * 60
_CANTEEN_COOKIE = "myschool_canteen_session"

def _signed_role_token(role: str, user_id: int, school_id: int, expires: int) -> str:
    payload = f"{role}:{user_id}:{school_id}:{expires}"
    signature = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

def _read_signed_role_token(request: Request, cookie_name: str, role: str, school_id: int):
    token = request.cookies.get(cookie_name, "")
    try:
        token_role, user_id, token_school_id, expires, signature = token.split(":", 4)
        payload = f"{token_role}:{user_id}:{token_school_id}:{expires}"
        expected = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if token_role != role or not hmac.compare_digest(signature, expected) or int(token_school_id) != school_id or int(expires) < int(time.time()):
            return None
        return int(user_id)
    except (ValueError, TypeError):
        return None

def _read_parent_session(request: Request, school_id: int):
    return _read_signed_role_token(request, _PARENT_COOKIE, "parent", school_id)

def _read_canteen_session(request: Request, school_name: str):
    token = request.cookies.get(_CANTEEN_COOKIE, "")
    try:
        role, school, expires, signature = token.split(":", 3)
        payload = f"{role}:{school}:{expires}"
        expected = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if role != "canteen" or school != school_name or not hmac.compare_digest(signature, expected) or int(expires) < int(time.time()):
            return False
        return True
    except (ValueError, TypeError):
        return False

def _make_canteen_token(school_name: str, expires: int) -> str:
    payload = f"canteen:{school_name}:{expires}"
    signature = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

@app.get("/{school_name}/parent")
def parent_portal_page(school_name: str, request: Request, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    parent_id = _read_parent_session(request, school.id)
    account = db.query(ParentAccount).filter(ParentAccount.id == parent_id, ParentAccount.school_id == school.id, ParentAccount.is_active == 1).first() if parent_id else None
    student = db.query(Student).filter(Student.id == account.student_id, ((Student.school_id == school.id) | (Student.school_id.is_(None)))).first() if account else None
    marks = db.query(StudentMark).filter(StudentMark.school_id == school.id, StudentMark.student_id == student.id, StudentMark.published == 1).order_by(StudentMark.academic_year.desc(), StudentMark.term, StudentMark.subject).all() if student else []
    return templates.TemplateResponse("parent_reports.html", {"request": request, "school": school, "parent": account, "student": student, "marks": marks})

@app.post("/{school_name}/parent/signup")
def parent_signup(school_name: str, request: Request, student_id: int = Form(...), link_code: str = Form(...), parent_name: str = Form(...), email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    email_clean = email.strip().lower()
    name_clean = parent_name.strip()[:120]
    if len(password) < 8 or len(password) > 128:
        raise HTTPException(status_code=400, detail="Use a password between 8 and 128 characters.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email_clean):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    student = db.query(Student).filter(Student.id == student_id, ((Student.school_id == school.id) | (Student.school_id.is_(None))), Student.is_active == 1).first()
    if not student or not student.parent_link_code or not hmac.compare_digest(str(student.parent_link_code).strip().upper(), link_code.strip().upper()):
        raise HTTPException(status_code=400, detail="Student ID or parent linking code is incorrect. Ask the school to issue a linking code.")
    if db.query(ParentAccount).filter(ParentAccount.school_id == school.id, ParentAccount.email == email_clean).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists for this school.")
    account = ParentAccount(school_id=school.id, student_id=student.id, parent_name=name_clean, email=email_clean, password_hash=_student_password_hash(password), is_active=1)
    db.add(account)
    try:
        db.commit()
        db.refresh(account)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not create parent account.")
    expires = int(time.time()) + _PARENT_SESSION_SECONDS
    token = _signed_role_token("parent", account.id, school.id, expires)
    response = RedirectResponse(f"/{school_name}/parent", status_code=303)
    response.set_cookie(_PARENT_COOKIE, token, max_age=_PARENT_SESSION_SECONDS, httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return response

@app.get("/{school_name}/parent/login")
def parent_login_page(school_name: str, request: Request, db: Session = Depends(get_db)):
    """Render the parent authentication page for direct browser visits."""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    parent_id = _read_parent_session(request, school.id)
    account = db.query(ParentAccount).filter(
        ParentAccount.id == parent_id,
        ParentAccount.school_id == school.id,
        ParentAccount.is_active == 1,
    ).first() if parent_id else None
    student = db.query(Student).filter(
        Student.id == account.student_id,
        ((Student.school_id == school.id) | (Student.school_id.is_(None))),
    ).first() if account else None
    return templates.TemplateResponse(
        "parent_reports.html",
        {"request": request, "school": school, "parent": account, "student": student, "marks": []},
    )


@app.post("/{school_name}/parent/login")
def parent_login(school_name: str, request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    account = db.query(ParentAccount).filter(ParentAccount.school_id == school.id, ParentAccount.email == email.strip().lower(), ParentAccount.is_active == 1).first()
    if not account or not _student_password_matches(password, account.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    expires = int(time.time()) + _PARENT_SESSION_SECONDS
    token = _signed_role_token("parent", account.id, school.id, expires)
    response = RedirectResponse(f"/{school_name}/parent", status_code=303)
    response.set_cookie(_PARENT_COOKIE, token, max_age=_PARENT_SESSION_SECONDS, httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return response

@app.post("/{school_name}/parent/logout")
def parent_logout(school_name: str):
    response = RedirectResponse(f"/{school_name}/parent", status_code=303)
    response.delete_cookie(_PARENT_COOKIE, path="/")
    return response

@app.post("/{school_name}/admin/marks")
def add_student_mark(school_name: str, request: Request, student_id: int = Form(...), subject: str = Form(...), term: str = Form(...), academic_year: str = Form(...), score: int = Form(...), max_score: int = Form(100), teacher_comment: str = Form(""), published: int = Form(0), db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student = db.query(Student).filter(Student.id == student_id, ((Student.school_id == school.id) | (Student.school_id.is_(None)))).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found in this school.")
    subject = subject.strip()[:100]
    term = term.strip()[:40]
    academic_year = academic_year.strip()[:20]
    if not subject or not term or not academic_year or max_score < 1 or score < 0 or score > max_score:
        raise HTTPException(status_code=400, detail="Enter a subject, term, academic year, and valid mark.")
    mark = StudentMark(school_id=school.id, student_id=student.id, subject=subject, term=term, academic_year=academic_year, score=score, max_score=max_score, teacher_comment=teacher_comment.strip()[:1000], published=1 if published else 0)
    db.add(mark); db.commit(); db.refresh(mark)
    return {"status":"success","mark_id":mark.id,"published":bool(mark.published)}

# ==================== TEACHER PAGES ====================
_TEACHER_COOKIE = "teacher_session"
_TEACHER_SESSION_SECONDS = 86400 * 7


def _read_teacher_session(request: Request, school_id: int):
    return _read_signed_role_token(request, _TEACHER_COOKIE, "teacher", school_id)


def _hmac_password(new_password: str) -> str:
    return _student_password_hash(new_password)


def _string_to_list(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _teacher_pair_conflicts(db: Session, school, class_names: list[str], subject_names: list[str], exclude_teacher_id=None):
    """Each (class, subject) combination can only be covered by one teacher.
    Subjects are not taught school-wide — other classes do not take them."""
    wanted = {(c.strip().lower(), s.strip().lower()) for c in class_names for s in subject_names}
    if not wanted:
        return []
    others = db.query(Teacher).filter(Teacher.school_id == school.id, Teacher.is_active == 1)
    if exclude_teacher_id:
        others = others.filter(Teacher.id != exclude_teacher_id)
    conflicts = []
    for other in others:
        pairs = {(c.strip().lower(), s.strip().lower()) for c in _string_to_list(other.classes) for s in _string_to_list(other.subjects)}
        overlap = wanted & pairs
        if overlap:
            conflicts.append(f"{other.full_name}: " + ", ".join(f"{cls} × {sub}" for cls, sub in sorted(overlap)))
    return conflicts


def _teacher_has_assignment(teacher: Teacher, field_value: str, wanted: str) -> bool:
    wanted_clean = (wanted or "").strip().lower()
    return wanted_clean in [item.lower() for item in _string_to_list(field_value)]


def _require_teacher(school_name: str, request: Request, db: Session):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    teacher_id = _read_teacher_session(request, school.id)
    if not teacher_id:
        raise HTTPException(status_code=401, detail="Teacher login required.")
    teacher = db.query(Teacher).filter(Teacher.id == teacher_id, Teacher.school_id == school.id, Teacher.is_active == 1).first()
    if not teacher:
        raise HTTPException(status_code=401, detail="Teacher login required.")
    return school, teacher


class TeacherMarkRow(BaseModel):
    student_id: int
    score: Optional[int] = None
    teacher_comment: Optional[str] = ""
    published: Optional[bool] = False


class TeacherMarksSave(BaseModel):
    subject: str
    term: str
    academic_year: str
    max_score: int = 100
    rows: List[TeacherMarkRow]


@app.get("/{school_name}/teacher")
def teacher_page(school_name: str, request: Request, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    teacher_id = _read_teacher_session(request, school.id)
    teacher = db.query(Teacher).filter(Teacher.id == teacher_id, Teacher.school_id == school.id, Teacher.is_active == 1).first() if teacher_id else None
    return templates.TemplateResponse("teacher.html", {
        "request": request,
        "school": school,
        "school_name": school_name,
        "teacher": teacher,
        "logged_in": bool(teacher),
        "teacher_is_dos": bool(teacher.is_dos) if teacher else False,
        "teacher_subjects": _string_to_list(teacher.subjects) if teacher else [],
        "teacher_classes": sorted(_string_to_list(teacher.classes)) if teacher else [],
    })


@app.post("/{school_name}/teacher/login")
def teacher_login(school_name: str, request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    email_clean = email.strip().lower()
    teacher = db.query(Teacher).filter(Teacher.school_id == school.id, Teacher.email == email_clean, Teacher.is_active == 1).first()
    if not teacher or not _student_password_matches(password, teacher.password_hash):
        return templates.TemplateResponse("teacher.html", {
            "request": request, "school": school, "school_name": school_name, "teacher": None,
            "logged_in": False, "teacher_subjects": [], "teacher_classes": [],
            "error": "Invalid email or password",
        })
    expires = int(time.time()) + _TEACHER_SESSION_SECONDS
    token = _signed_role_token("teacher", teacher.id, school.id, expires)
    response = RedirectResponse(f"/{school_name}/teacher", status_code=303)
    response.set_cookie(_TEACHER_COOKIE, token, max_age=_TEACHER_SESSION_SECONDS, httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return response


@app.post("/{school_name}/teacher/logout")
def teacher_logout(school_name: str):
    response = RedirectResponse(f"/{school_name}/teacher", status_code=303)
    response.delete_cookie(_TEACHER_COOKIE)
    return response


@app.get("/{school_name}/teacher/api/table")
def teacher_marks_table(
    school_name: str,
    request: Request,
    class_name: str = Query(..., min_length=1),
    subject: str = Query(..., min_length=1),
    term: str = Query(..., min_length=1),
    academic_year: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Auto table maker: builds the marks entry table for one class + subject + term."""
    school, teacher = _require_teacher(school_name, request, db)
    class_name = class_name.strip()
    subject_clean = subject.strip()[:100]
    term_clean = term.strip()[:40]
    year_clean = academic_year.strip()[:20]
    if not _teacher_has_assignment(teacher, teacher.classes, class_name):
        raise HTTPException(status_code=403, detail="You are not assigned to this class.")
    if not _teacher_has_assignment(teacher, teacher.subjects, subject_clean):
        raise HTTPException(status_code=403, detail="You are not assigned to teach this subject.")

    students = db.query(Student).filter(
        (Student.school_id == school.id) | (Student.school_id.is_(None)),
        Student.class_name == class_name,
        Student.is_active == 1,
    ).order_by(Student.student_name).all()
    student_ids = [s.id for s in students]
    marks = db.query(StudentMark).filter(
        StudentMark.school_id == school.id,
        StudentMark.student_id.in_(student_ids),
        StudentMark.subject == subject_clean,
        StudentMark.term == term_clean,
        StudentMark.academic_year == year_clean,
    ).all() if student_ids else []
    marks_by_student = {m.student_id: m for m in marks}

    rows = []
    for s in students:
        m = marks_by_student.get(s.id)
        rows.append({
            "student_id": s.id,
            "student_name": s.student_name,
            "mark_id": m.id if m else None,
            "score": m.score if m else None,
            "score_in_percent": round(m.score * 100 / m.max_score) if m else None,
            "max_score": m.max_score if m else 100,
            "teacher_comment": m.teacher_comment if m else "",
            "published": bool(m.published) if m else False,
            "entered_by_me": bool(m and m.teacher_id == teacher.id) if m else False,
        })

    entered = [r["score"] / r["max_score"] * 100 for r in rows if r["score"] is not None]
    stats = {
        "students": len(rows),
        "entered": len(entered),
        "average_percent": round(sum(entered) / len(entered)) if entered else None,
        "highest_percent": round(max(entered)) if entered else None,
        "lowest_percent": round(min(entered)) if entered else None,
        "pass_count": sum(1 for p in entered if p >= 50),
    }
    return {"status": "success", "class_name": class_name, "subject": subject_clean, "term": term_clean,
            "academic_year": year_clean, "rows": rows, "stats": stats}


@app.post("/{school_name}/teacher/api/marks")
def teacher_save_marks(school_name: str, request: Request, payload: TeacherMarksSave, db: Session = Depends(get_db)):
    """Bulk save (upsert) marks for the students in the generated table."""
    school, teacher = _require_teacher(school_name, request, db)
    subject_clean = payload.subject.strip()[:100]
    term_clean = payload.term.strip()[:40]
    year_clean = payload.academic_year.strip()[:20]
    max_score = payload.max_score
    if not subject_clean or not term_clean or not year_clean or max_score < 1:
        raise HTTPException(status_code=400, detail="Subject, term, academic year and max score are required.")
    if not payload.rows:
        raise HTTPException(status_code=400, detail="Enter at least one mark.")

    # All rows in one table belong to one class; validate with the first student.
    first_student = db.query(Student).filter(Student.id == payload.rows[0].student_id,
                                             ((Student.school_id == school.id) | (Student.school_id.is_(None)))).first()
    if not first_student:
        raise HTTPException(status_code=404, detail="Student not found in this school.")
    if not _teacher_has_assignment(teacher, teacher.classes, first_student.class_name):
        raise HTTPException(status_code=403, detail="You are not assigned to this class.")
    if not _teacher_has_assignment(teacher, teacher.subjects, subject_clean):
        raise HTTPException(status_code=403, detail="You are not assigned to teach this subject.")

    saved, skipped = [], []
    for row in payload.rows:
        student = db.query(Student).filter(Student.id == row.student_id,
                                           ((Student.school_id == school.id) | (Student.school_id.is_(None)))).first()
        if not student:
            skipped.append(row.student_id); continue
        existing = db.query(StudentMark).filter(
            StudentMark.school_id == school.id,
            StudentMark.student_id == student.id,
            StudentMark.subject == subject_clean,
            StudentMark.term == term_clean,
            StudentMark.academic_year == year_clean,
        ).first()
        if row.score is None:
            if existing:
                db.delete(existing)
            skipped.append(student.id); continue
        if row.score < 0 or row.score > max_score:
            skipped.append(student.id); continue
        if existing:
            existing.score = row.score
            existing.max_score = max_score
            existing.teacher_comment = (row.teacher_comment or "").strip()[:1000]
            existing.published = 1 if row.published else 0
            existing.teacher_id = teacher.id
        else:
            existing = StudentMark(school_id=school.id, student_id=student.id, teacher_id=teacher.id,
                                   subject=subject_clean, term=term_clean, academic_year=year_clean,
                                   score=row.score, max_score=max_score,
                                   teacher_comment=(row.teacher_comment or "").strip()[:1000],
                                   published=1 if row.published else 0)
            db.add(existing)
        saved.append(student.id)
    db.commit()
    return {"status": "success", "saved": len(saved), "skipped": skipped}


# ==================== TEACHER MANAGEMENT (ADMIN) ====================
@app.get("/{school_name}/admin/teachers")
def list_teachers(school_name: str, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    teachers = db.query(Teacher).filter(Teacher.school_id == school.id).order_by(Teacher.created_at.desc()).all()
    return {"status": "success", "teachers": [{
        "id": t.id, "full_name": t.full_name, "email": t.email, "username": t.username,
        "subjects": _string_to_list(t.subjects), "classes": sorted(_string_to_list(t.classes)),
        "is_dos": bool(t.is_dos),
        "is_active": bool(t.is_active), "created_at": t.created_at.strftime("%Y-%m-%d") if t.created_at else "",
    } for t in teachers]}


@app.post("/{school_name}/admin/teachers")
def create_teacher(
    school_name: str,
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    username: Optional[str] = Form(None),
    subjects: str = Form(""),
    classes: str = Form(""),
    db: Session = Depends(get_db),
):
    """Create a teacher: names, email (used to log in), password, one or many subjects and classes."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    name_clean = full_name.strip()[:120]
    email_clean = email.strip().lower()
    subject_list = _string_to_list(subjects)
    class_list = _string_to_list(classes)
    if not name_clean or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email_clean):
        raise HTTPException(status_code=400, detail="Full name and a valid email address are required.")
    if len(password) < 8 or len(password) > 128:
        raise HTTPException(status_code=400, detail="Password must be 8-128 characters.")
    if not subject_list or not class_list:
        raise HTTPException(status_code=400, detail="Assign at least one subject and one class to the teacher — a teacher may teach many subjects.")
    if len(subject_list) > 10 or len(class_list) > 12 or any(len(s) > 100 for s in subject_list):
        raise HTTPException(status_code=400, detail="Too many subjects/classes assigned.")
    conflicts = _teacher_pair_conflicts(db, school, class_list, subject_list)
    if conflicts:
        raise HTTPException(status_code=409, detail="A teacher teaches each subject in one class only. Already covered by: " + "; ".join(conflicts))
    # Teachers log in with their email; the username is only a unique key derived from it.
    username_clean = (username or "").strip().lower()
    if not username_clean:
        username_clean = re.sub(r"[^a-z0-9_.-]", "_", email_clean.split("@")[0])[:60] or "teacher"
    if not re.fullmatch(r"[a-z0-9_.-]{3,80}", username_clean):
        raise HTTPException(status_code=400, detail="Username must be 3-80 letters/numbers or ._-")
    suffix = 2
    while db.query(Teacher).filter(Teacher.school_id == school.id, Teacher.username == username_clean).first():
        stem = re.sub(r"[0-9]+$", "", username_clean) or username_clean
        username_clean = f"{stem[:70]}{suffix}"
        suffix += 1
    teacher = Teacher(school_id=school.id, full_name=name_clean, email=email_clean, username=username_clean,
                      password_hash=_student_password_hash(password),
                      subjects=", ".join(subject_list)[:300], classes=", ".join(class_list)[:200], is_active=1)
    db.add(teacher)
    try:
        db.commit(); db.refresh(teacher)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not create teacher account.")
    return {"status": "success", "teacher_id": teacher.id, "username": username_clean, "email": email_clean}


@app.delete("/{school_name}/admin/teachers/{teacher_id}")
def delete_teacher(school_name: str, teacher_id: int, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    teacher = db.query(Teacher).filter(Teacher.id == teacher_id, Teacher.school_id == school.id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found.")
    db.delete(teacher); db.commit()
    return {"status": "success"}


@app.get("/{school_name}/admin/marks/report")
def admin_marks_report(
    school_name: str,
    request: Request,
    class_name: str = Query(..., min_length=1),
    term: str = Query(..., min_length=1),
    academic_year: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Class marks report: students x subjects matrix with averages, for admin tracking."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    class_clean = class_name.strip()
    term_clean = term.strip()[:40]
    year_clean = academic_year.strip()[:20]

    students = db.query(Student).filter(
        (Student.school_id == school.id) | (Student.school_id.is_(None)),
        Student.class_name == class_clean,
    ).order_by(Student.is_active.desc(), Student.student_name).all()
    student_ids = [s.id for s in students]
    marks = db.query(StudentMark).filter(
        StudentMark.school_id == school.id,
        StudentMark.student_id.in_(student_ids),
        StudentMark.term == term_clean,
        StudentMark.academic_year == year_clean,
    ).all() if student_ids else []

    subjects = sorted({m.subject for m in marks})
    by_student: dict[int, list] = {}
    for m in marks:
        by_student.setdefault(m.student_id, []).append(m)

    def _percent(m):
        return round(m.score * 100 / m.max_score) if m.max_score else 0

    rows = []
    all_percents = []
    subject_percents: dict[str, list] = {s: [] for s in subjects}
    for s in students:
        student_marks = by_student.get(s.id, [])
        scores = {}
        student_percents = []
        for m in student_marks:
            p = _percent(m)
            scores[m.subject] = {"percent": p, "score": m.score, "max_score": m.max_score,
                                 "comment": m.teacher_comment or "", "published": bool(m.published), "mark_id": m.id}
            student_percents.append(p)
            subject_percents[m.subject].append(p)
            all_percents.append(p)
        rows.append({
            "student_id": s.id, "student_name": s.student_name, "is_active": s.is_active != 0,
            "scores": scores,
            "average_percent": round(sum(student_percents) / len(student_percents)) if student_percents else None,
            "subjects_count": len(student_percents),
        })

    subject_averages = {s: round(sum(v) / len(v)) for s, v in subject_percents.items() if v}
    return {"status": "success", "class_name": class_clean, "term": term_clean, "academic_year": year_clean,
            "subjects": subjects, "rows": rows,
            "class_average": round(sum(all_percents) / len(all_percents)) if all_percents else None,
            "subject_averages": subject_averages}


@app.delete("/{school_name}/admin/marks/{mark_id}")
def delete_mark(school_name: str, mark_id: int, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    mark = db.query(StudentMark).filter(StudentMark.id == mark_id, StudentMark.school_id == school.id).first()
    if not mark:
        raise HTTPException(status_code=404, detail="Mark not found.")
    db.delete(mark); db.commit()
    return {"status": "success"}


@app.get("/{school_name}/export/marks")
def export_marks_to_excel(
    school_name: str,
    request: Request,
    class_name: str = Query(..., min_length=1),
    term: str = Query(..., min_length=1),
    academic_year: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    try:
        class_clean = class_name.strip(); term_clean = term.strip(); year_clean = academic_year.strip()
        marks = db.query(StudentMark, Student).join(Student, StudentMark.student_id == Student.id).filter(
            StudentMark.school_id == school.id,
            Student.class_name == class_clean,
            StudentMark.term == term_clean,
            StudentMark.academic_year == year_clean,
        ).order_by(Student.class_name, Student.student_name, StudentMark.subject).all()
        data = [{
            "Student": student.student_name,
            "Class": student.class_name,
            "Subject": mark.subject,
            "Term": mark.term,
            "Year": mark.academic_year,
            "Score": mark.score,
            "Out of": mark.max_score,
            "Percent": round(mark.score * 100 / mark.max_score) if mark.max_score else 0,
            "Published": "Yes" if mark.published else "No",
            "Comment": mark.teacher_comment or "",
        } for mark, student in marks]
        if not data:
            return JSONResponse({"error": "No marks found for this report."}, status_code=404)
        df = pd.DataFrame(data)
        filename = f"marks_{school_name}_{class_clean}_{term_clean}_{year_clean}.xlsx".replace(" ", "_")
        filepath = f"/tmp/{filename}"
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Marks', index=False)
            worksheet = writer.sheets['Marks']
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except: pass
                worksheet.column_dimensions[column_letter].width = min(max_length + 2, 50)
            header_fill = PatternFill(start_color="1F8A54", end_color="1F8A54", fill_type="solid")
            header_font = Font(bold=True, color="FFFFFF")
            for cell in worksheet[1]:
                cell.fill = header_fill
                cell.font = header_font
        return FileResponse(filepath, filename=filename, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ==================== FULL CRUD MANAGEMENT (ADMIN) ====================
class EatsMenuUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price_rwf: Optional[int] = None
    available: Optional[bool] = None


@app.put("/{school_name}/admin/teachers/{teacher_id}")
def update_teacher(
    school_name: str,
    teacher_id: int,
    request: Request,
    full_name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    username: Optional[str] = Form(None),
    subjects: Optional[str] = Form(None),
    classes: Optional[str] = Form(None),
    is_active: Optional[int] = Form(None),
    is_dos: Optional[int] = Form(None),
    new_password: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Edit a teacher: names, email, username, subjects, classes, DOS/active flags or password reset."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    teacher = db.query(Teacher).filter(Teacher.id == teacher_id, Teacher.school_id == school.id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    if full_name is not None and full_name.strip():
        teacher.full_name = full_name.strip()[:120]
    if email is not None:
        email_clean = email.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email_clean):
            raise HTTPException(status_code=400, detail="Enter a valid email address.")
        clash = db.query(Teacher).filter(Teacher.school_id == school.id, Teacher.email == email_clean, Teacher.id != teacher_id).first()
        if clash:
            raise HTTPException(status_code=409, detail="Another teacher already uses this email.")
        teacher.email = email_clean
    if username is not None:
        username_clean = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]{3,80}", username_clean):
            raise HTTPException(status_code=400, detail="Username must be 3-80 letters/numbers or ._-")
        clash = db.query(Teacher).filter(Teacher.school_id == school.id, Teacher.username == username_clean, Teacher.id != teacher_id).first()
        if clash:
            raise HTTPException(status_code=409, detail="Another teacher already uses this username.")
        teacher.username = username_clean
    if subjects is not None:
        subject_list = _string_to_list(subjects)
        if not subject_list:
            raise HTTPException(status_code=400, detail="A teacher must teach at least one subject.")
        teacher.subjects = ", ".join(subject_list)[:300]
    if classes is not None:
        class_list = _string_to_list(classes)
        if not class_list:
            raise HTTPException(status_code=400, detail="A teacher must be assigned to at least one class.")
        teacher.classes = ", ".join(class_list)[:200]
    if is_active is not None:
        teacher.is_active = 1 if is_active else 0
    if is_dos is not None:
        teacher.is_dos = 1 if is_dos else 0
    if new_password:
        if len(new_password) < 8 or len(new_password) > 128:
            raise HTTPException(status_code=400, detail="New password must be 8-128 characters.")
        teacher.password_hash = _hmac_password(new_password)
    final_classes = _string_to_list(classes) if classes is not None else _string_to_list(teacher.classes)
    final_subjects = _string_to_list(subjects) if subjects is not None else _string_to_list(teacher.subjects)
    conflicts = _teacher_pair_conflicts(db, school, final_classes, final_subjects, exclude_teacher_id=teacher.id)
    if conflicts:
        db.rollback()
        raise HTTPException(status_code=409, detail="A teacher teaches each subject in one class only. Already covered by: " + "; ".join(conflicts))
    db.commit()
    return {"status": "success", "id": teacher.id}


@app.get("/{school_name}/admin/canteen-admins")
def list_canteen_admins(school_name: str, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    staff = db.query(CanteenAdmin).filter(CanteenAdmin.school_id == school.id).order_by(CanteenAdmin.created_at.desc()).all()
    return {"status": "success", "accounts": [{
        "id": a.id, "username": a.username, "is_active": bool(a.is_active),
        "created_at": a.created_at.strftime("%Y-%m-%d") if a.created_at else "",
    } for a in staff]}


@app.put("/{school_name}/admin/canteen-admins/{admin_id}")
def update_canteen_admin(
    school_name: str, admin_id: int, request: Request,
    is_active: Optional[int] = Form(None), new_password: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    staff = db.query(CanteenAdmin).filter(CanteenAdmin.id == admin_id, CanteenAdmin.school_id == school.id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Canteen account not found.")
    if is_active is not None:
        staff.is_active = 1 if is_active else 0
    if new_password:
        if len(new_password) < 8 or len(new_password) > 128:
            raise HTTPException(status_code=400, detail="New password must be 8-128 characters.")
        staff.password_hash = _hmac_password(new_password)
    db.commit()
    return {"status": "success", "id": staff.id}


@app.delete("/{school_name}/admin/canteen-admins/{admin_id}")
def delete_canteen_admin(school_name: str, admin_id: int, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    staff = db.query(CanteenAdmin).filter(CanteenAdmin.id == admin_id, CanteenAdmin.school_id == school.id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Canteen account not found.")
    db.delete(staff); db.commit()
    return {"status": "success"}


@app.get("/{school_name}/admin/parents")
def list_parent_accounts(school_name: str, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    students = {s.id: s for s in db.query(Student).filter((Student.school_id == school.id) | (Student.school_id.is_(None))).all()}
    accounts = db.query(ParentAccount).filter(ParentAccount.school_id == school.id).order_by(ParentAccount.created_at.desc()).all()
    return {"status": "success", "accounts": [{
        "id": a.id, "parent_name": a.parent_name, "email": a.email,
        "student_id": a.student_id, "student_name": (students.get(a.student_id).student_name if students.get(a.student_id) else ""),
        "student_class": (students.get(a.student_id).class_name if students.get(a.student_id) else ""),
        "is_active": bool(a.is_active),
        "created_at": a.created_at.strftime("%Y-%m-%d") if a.created_at else "",
    } for a in accounts]}


@app.put("/{school_name}/admin/parents/{account_id}")
def update_parent_account(
    school_name: str, account_id: int, request: Request,
    is_active: Optional[int] = Form(None), new_password: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    account = db.query(ParentAccount).filter(ParentAccount.id == account_id, ParentAccount.school_id == school.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Parent account not found.")
    if is_active is not None:
        account.is_active = 1 if is_active else 0
    if new_password:
        if len(new_password) < 8 or len(new_password) > 128:
            raise HTTPException(status_code=400, detail="New password must be 8-128 characters.")
        account.password_hash = _hmac_password(new_password)
    db.commit()
    return {"status": "success", "id": account.id}


@app.delete("/{school_name}/admin/parents/{account_id}")
def delete_parent_account(school_name: str, account_id: int, request: Request, db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    account = db.query(ParentAccount).filter(ParentAccount.id == account_id, ParentAccount.school_id == school.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Parent account not found.")
    db.delete(account); db.commit()
    return {"status": "success"}


@app.put("/{school_name}/admin/marks/{mark_id}")
def update_mark(
    school_name: str, mark_id: int, request: Request,
    score: Optional[int] = Form(None), max_score: Optional[int] = Form(None),
    teacher_comment: Optional[str] = Form(None), published: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """Admin can correct a mark, change its out-of value, comment or publish state."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    mark = db.query(StudentMark).filter(StudentMark.id == mark_id, StudentMark.school_id == school.id).first()
    if not mark:
        raise HTTPException(status_code=404, detail="Mark not found.")
    new_max = max_score if max_score is not None else mark.max_score
    new_score = score if score is not None else mark.score
    if not new_max or new_max < 1:
        raise HTTPException(status_code=400, detail="Maximum score must be at least 1.")
    if new_score is None or new_score < 0 or new_score > new_max:
        raise HTTPException(status_code=400, detail=f"Score must be between 0 and {new_max}.")
    mark.max_score = new_max
    mark.score = new_score
    if teacher_comment is not None:
        mark.teacher_comment = teacher_comment.strip()[:1000]
    if published is not None:
        mark.published = 1 if published else 0
    db.commit()
    return {"status": "success", "id": mark.id, "percent": round(mark.score * 100 / mark.max_score), "published": bool(mark.published)}


@app.put("/{school_name}/admin/visits/{visit_id}")
def update_visit(
    school_name: str, visit_id: int, request: Request,
    movement_method: Optional[str] = Form(None),
    arrival_plate_number: Optional[str] = Form(None),
    parent_name: Optional[str] = Form(None),
    parent_relationship: Optional[str] = Form(None),
    parent_phone: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Correct a visit record: how the student travelled and parent/car details."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    visit = db.query(Visit).join(Student, Visit.student_id == Student.id).filter(
        Visit.id == visit_id, (Student.school_id == school.id) | (Student.school_id.is_(None))).first()
    if not visit:
        raise HTTPException(status_code=404, detail="Visit record not found in this school.")
    if movement_method is not None:
        value = movement_method.strip()
        if value and value not in ("with_car", "without_car"):
            raise HTTPException(status_code=400, detail="Movement must be with_car or without_car (or empty).")
        visit.movement_method = value or None
    if arrival_plate_number is not None:
        visit.arrival_plate_number = arrival_plate_number.strip()[:30] or None
    if parent_name is not None:
        visit.parent_name = parent_name.strip()[:120] or None
    if parent_relationship is not None:
        relation = parent_relationship.strip().lower()
        if relation and relation not in ("father", "mother", "brother", "sister", "other"):
            raise HTTPException(status_code=400, detail="Relationship must be father, mother, brother, sister or other (or empty).")
        visit.parent_relationship = relation or None
    if parent_phone is not None:
        visit.parent_phone = parent_phone.strip()[:30] or None
    db.commit()
    return {"status": "success", "id": visit.id}


# ---- Eats menu: full management for canteen staff/admin (update item, price, availability) ----
@app.put("/{school_name}/eats/api/menu/{item_id}")
def eats_update_menu_item(school_name: str, item_id: int, payload: EatsMenuUpdate, request: Request, db: Session = Depends(get_db)):
    if not (_check_admin(request, school_name) or _read_canteen_session(request, school_name)):
        raise HTTPException(status_code=401, detail="School admin or canteen login required")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    item = db.query(EatsItem).filter(EatsItem.id == item_id, EatsItem.school_id == school.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    if payload.name is not None:
        item.name = payload.name.strip()[:120]
    if payload.description is not None:
        item.description = payload.description.strip()[:300]
    if payload.price_rwf is not None:
        if payload.price_rwf < 1 or payload.price_rwf > 1000000:
            raise HTTPException(status_code=400, detail="Price must be between 1 and 1000000 RWF")
        item.price_cents = payload.price_rwf
    if payload.available is not None:
        # Keep the item instead of deleting it so past transactions stay traceable.
        item.available = 1 if payload.available else 0
    db.commit(); db.refresh(item)
    return {"id": item.id, "name": item.name, "description": item.description or "", "price_rwf": item.price_cents, "available": bool(item.available)}


@app.get("/{school_name}/eats/api/menu/manage")
def eats_manage_menu(school_name: str, request: Request, db: Session = Depends(get_db)):
    if not (_check_admin(request, school_name) or _read_canteen_session(request, school_name)):
        raise HTTPException(status_code=401, detail="School admin or canteen login required")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    items = db.query(EatsItem).filter(EatsItem.school_id == school.id).order_by(EatsItem.available.desc(), EatsItem.name).all()
    return [{"id": x.id, "name": x.name, "description": x.description or "", "price_rwf": x.price_cents, "available": bool(x.available)} for x in items]


# ==================== TIMETABLES (DOS / ADMIN) ====================
_VAULT_COOKIE = "vault_session"
_VAULT_SESSION_SECONDS = 600  # vault stays open for 10 minutes after unlocking


def _make_vault_token(school_name: str, expires: int) -> str:
    payload = f"vault:{school_name}:{expires}"
    signature = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def _read_vault_session(request: Request, school_name: str) -> bool:
    token = request.cookies.get(_VAULT_COOKIE, "")
    try:
        role, school, expires, signature = token.split(":", 3)
        payload = f"{role}:{school}:{expires}"
        expected = hmac.new(_signing_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        return role == "vault" and school == school_name and hmac.compare_digest(signature, expected) and int(expires) >= int(time.time())
    except (ValueError, TypeError):
        return False


@app.post("/{school_name}/admin/vault/unlock")
def vault_unlock(school_name: str, request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    """Extra re-authentication step before the Default Access vault opens."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    expected = f"{school_name}001"
    if not hmac.compare_digest(password.strip(), expected):
        raise HTTPException(status_code=403, detail="Wrong vault password.")
    expires = int(time.time()) + _VAULT_SESSION_SECONDS
    response = JSONResponse({"status": "success", "valid_for_minutes": _VAULT_SESSION_SECONDS // 60})
    response.set_cookie(_VAULT_COOKIE, _make_vault_token(school_name, expires), max_age=_VAULT_SESSION_SECONDS, httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return response


@app.get("/{school_name}/admin/vault/secrets")
def vault_secrets(school_name: str, request: Request):
    """Vault values are only released after the unlock step."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    if not _read_vault_session(request, school_name):
        raise HTTPException(status_code=403, detail="Vault is locked. Unlock first.")
    return {"status": "success", "admin_default_password": f"{school_name}001", "expires_minutes": _VAULT_SESSION_SECONDS // 60}


def _get_dos_actor(school_name: str, request: Request, db: Session):
    """Timetables can be built by the school admin or by a teacher flagged as DOS."""
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    if _check_admin(request, school_name):
        return school, "admin", None
    teacher_id = _read_teacher_session(request, school.id)
    if teacher_id:
        teacher = db.query(Teacher).filter(Teacher.id == teacher_id, Teacher.school_id == school.id, Teacher.is_active == 1, Teacher.is_dos == 1).first()
        if teacher:
            return school, "teacher", teacher
    raise HTTPException(status_code=401, detail="Dean of Studies or school administrator login required.")


class TimetableSlotInput(BaseModel):
    class_name: str
    day: int
    period: int
    subject: str
    teacher_id: Optional[int] = None


TIMETABLE_DAYS = 5      # Monday..Friday
TIMETABLE_PERIODS = 8


def _class_subject_options(school_id: int, class_name: str, db: Session):
    """Which subjects exist for this class, straight from teacher assignments."""
    subject_options: dict[str, list] = {}
    wanted = (class_name or "").strip().lower()
    for t in db.query(Teacher).filter(Teacher.school_id == school_id, Teacher.is_active == 1).all():
        if wanted not in [c.strip().lower() for c in _string_to_list(t.classes)]:
            continue
        for s in _string_to_list(t.subjects):
            subject_options.setdefault(s, []).append({"teacher_id": t.id, "teacher_name": t.full_name})
    return subject_options


@app.get("/{school_name}/admin/timetable")
def get_timetable(school_name: str, request: Request, class_name: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    school, _, _ = _get_dos_actor(school_name, request, db)
    class_clean = class_name.strip()[:50]
    slots = db.query(TimetableSlot).filter(
        TimetableSlot.school_id == school.id, TimetableSlot.class_name == class_clean
    ).order_by(TimetableSlot.day, TimetableSlot.period).all()
    slot_rows = []
    for s in slots:
        slot_rows.append({"id": s.id, "day": s.day, "period": s.period, "subject": s.subject, "teacher_id": s.teacher_id})
    return {"status": "success", "class_name": class_clean, "slots": slot_rows,
            "subject_options": _class_subject_options(school.id, class_clean, db)}


@app.put("/{school_name}/admin/timetable")
def set_timetable_slot(school_name: str, request: Request, payload: TimetableSlotInput, db: Session = Depends(get_db)):
    school, _, _ = _get_dos_actor(school_name, request, db)
    class_clean = payload.class_name.strip()[:50]
    subject_clean = payload.subject.strip()[:100]
    if not (1 <= payload.day <= TIMETABLE_DAYS and 1 <= payload.period <= TIMETABLE_PERIODS):
        raise HTTPException(status_code=400, detail="Day must be 1-5 and period 1-8.")
    if not subject_clean:
        raise HTTPException(status_code=400, detail="Choose a subject for the slot.")

    slot = db.query(TimetableSlot).filter(
        TimetableSlot.school_id == school.id, TimetableSlot.class_name == class_clean,
        TimetableSlot.day == payload.day, TimetableSlot.period == payload.period,
    ).first()

    new_teacher_id = payload.teacher_id
    if new_teacher_id:
        teacher = db.query(Teacher).filter(Teacher.id == new_teacher_id, Teacher.school_id == school.id, Teacher.is_active == 1).first()
        if not teacher:
            raise HTTPException(status_code=404, detail="Teacher not found in this school.")
        if not _teacher_has_assignment(teacher, teacher.classes, class_clean):
            raise HTTPException(status_code=400, detail=f"{teacher.full_name} is not assigned to {class_clean}.")
        if not _teacher_has_assignment(teacher, teacher.subjects, subject_clean):
            raise HTTPException(status_code=400, detail=f"{teacher.full_name} does not teach {subject_clean}.")
        clash = db.query(TimetableSlot).filter(
            TimetableSlot.school_id == school.id,
            TimetableSlot.teacher_id == new_teacher_id,
            TimetableSlot.day == payload.day,
            TimetableSlot.period == payload.period,
        ).first()
        if clash and (slot is None or clash.id != slot.id):
            raise HTTPException(status_code=409, detail=f"That teacher already has {clash.class_name} ({clash.subject}) in this slot.")

    if slot is None:
        slot = TimetableSlot(school_id=school.id, class_name=class_clean, day=payload.day, period=payload.period, subject=subject_clean, teacher_id=new_teacher_id)
        db.add(slot)
    else:
        slot.subject = subject_clean
        slot.teacher_id = new_teacher_id
    db.commit(); db.refresh(slot)
    return {"status": "success", "slot": {"id": slot.id, "day": slot.day, "period": slot.period, "subject": slot.subject, "teacher_id": slot.teacher_id}}


@app.delete("/{school_name}/admin/timetable/{slot_id}")
def clear_timetable_slot(school_name: str, slot_id: int, request: Request, db: Session = Depends(get_db)):
    school, _, _ = _get_dos_actor(school_name, request, db)
    slot = db.query(TimetableSlot).filter(TimetableSlot.id == slot_id, TimetableSlot.school_id == school.id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found.")
    db.delete(slot); db.commit()
    return {"status": "success"}


@app.get("/{school_name}/teacher/api/students")
def teacher_student_rosters(school_name: str, request: Request, class_name: str = Query(None), db: Session = Depends(get_db)):
    """Teachers can see the students in the classes they are assigned to."""
    school, teacher = _require_teacher(school_name, request, db)
    assigned = [c for c in _string_to_list(teacher.classes)]
    wanted = [c.strip() for c in assigned]
    if class_name:
        class_clean = class_name.strip()
        if class_clean.lower() not in [c.lower() for c in assigned]:
            raise HTTPException(status_code=403, detail="You are not assigned to this class.")
        wanted = [class_clean]
    roster = {}
    for c in sorted(set(wanted)):
        students = db.query(Student).filter(
            (Student.school_id == school.id) | (Student.school_id.is_(None)),
            Student.class_name == c, Student.is_active == 1,
        ).order_by(Student.student_name).all()
        roster[c] = [{"id": s.id, "student_name": s.student_name} for s in students]
    return {"status": "success", "my_classes": sorted(set(wanted)), "roster": roster}


@app.get("/{school_name}/teacher/api/my-timetable")
def teacher_my_timetable(school_name: str, request: Request, db: Session = Depends(get_db)):
    """The teacher's personal timetable: every slot they teach, across all their classes."""
    school, teacher = _require_teacher(school_name, request, db)
    slots = db.query(TimetableSlot).filter(
        TimetableSlot.school_id == school.id, TimetableSlot.teacher_id == teacher.id,
    ).order_by(TimetableSlot.day, TimetableSlot.period).all()
    return {"status": "success", "teacher_name": teacher.full_name, "slots": [
        {"class_name": s.class_name, "day": s.day, "period": s.period, "subject": s.subject} for s in slots
    ], "days": TIMETABLE_DAYS, "periods": TIMETABLE_PERIODS}


# ==================== CANTEEN ADMIN ACCESS ====================
@app.get("/{school_name}/canteen-admin")
def canteen_admin_page(school_name: str, request: Request, db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    if not _read_canteen_session(request, school_name):
        return templates.TemplateResponse("canteen_admin.html", {"request": request, "school": school, "logged_in": False})
    return templates.TemplateResponse("canteen_admin.html", {"request": request, "school": school, "logged_in": True})

@app.post("/{school_name}/admin/canteen-admins")
def create_canteen_admin(school_name: str, request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    username_clean = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,80}", username_clean) or len(password) < 8 or len(password) > 128:
        raise HTTPException(status_code=400, detail="Username must be 3–80 letters/numbers or ._-; password must be 8–128 characters.")
    if db.query(CanteenAdmin).filter(CanteenAdmin.school_id == school.id, CanteenAdmin.username == username_clean).first():
        raise HTTPException(status_code=409, detail="That canteen username already exists.")
    staff = CanteenAdmin(school_id=school.id, username=username_clean, password_hash=_student_password_hash(password), is_active=1)
    db.add(staff); db.commit()
    return {"status":"success","username":username_clean}

@app.post("/{school_name}/canteen-admin/login")
def canteen_admin_login(school_name: str, request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    staff = db.query(CanteenAdmin).filter(CanteenAdmin.school_id == school.id, CanteenAdmin.username == username.strip().lower(), CanteenAdmin.is_active == 1).first()
    if not staff or not _student_password_matches(password, staff.password_hash):
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    response = RedirectResponse(f"/{school_name}/canteen-admin", status_code=303)
    response.set_cookie(_CANTEEN_COOKIE, _make_canteen_token(school_name, int(time.time()) + _PARENT_SESSION_SECONDS), max_age=_PARENT_SESSION_SECONDS, httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")
    return response

@app.post("/{school_name}/canteen-admin/logout")
def canteen_admin_logout(school_name: str):
    response = RedirectResponse(f"/{school_name}/canteen-admin", status_code=303)
    response.delete_cookie(_CANTEEN_COOKIE, path="/")
    return response


# ==================== FIVE-DIGIT STUDENT CANTEEN IDS ====================
def _ensure_student_eats_card(db: Session, school_id: int, student_id: int, card_code: str):
    """Create a zero-balance canteen account for manual-ID checkout; NFC can be linked later."""
    existing = db.query(EatsCard).filter(EatsCard.school_id == school_id, EatsCard.student_id == student_id, EatsCard.active == 1).first()
    if existing:
        return existing
    internal_hash = _hashlib.sha256(f"myschool-student-card:{school_id}:{student_id}:{card_code}".encode("utf-8")).hexdigest()
    card = EatsCard(school_id=school_id, student_id=student_id, uid_hash=internal_hash, balance_cents=0, active=1)
    db.add(card)
    db.flush()
    return card


def _new_student_canteen_card_id(db: Session, school_id: int) -> str:
    """Issue a unique five-digit code for a school; this is a lookup ID, not a secret."""
    for _ in range(1000):
        code = str(secrets.randbelow(90000) + 10000)
        exists = db.query(Student.id).filter(Student.school_id == school_id, Student.canteen_card_id == code).first()
        if not exists:
            return code
    raise HTTPException(status_code=503, detail="Could not allocate a student card ID. Please retry.")


@app.post("/{school_name}/admin/canteen-cards/generate")
def generate_student_canteen_card_ids(school_name: str, request: Request, db: Session = Depends(get_db)):
    """Generate five-digit IDs for active students who do not have one yet."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    students = db.query(Student).filter(Student.school_id == school.id, Student.is_active == 1, Student.canteen_card_id.is_(None)).order_by(Student.id).all()
    created = 0
    try:
        for student in students:
            student.canteen_card_id = _new_student_canteen_card_id(db, school.id)
            db.flush()  # Session autoflush is disabled; flush so the next uniqueness check sees this ID.
            created += 1
        for student in db.query(Student).filter(Student.school_id == school.id, Student.is_active == 1, Student.canteen_card_id.is_not(None)).all():
            _ensure_student_eats_card(db, school.id, student.id, student.canteen_card_id)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not generate student card IDs. No changes were saved.")
    return {"status": "success", "generated": created, "message": f"Generated {created} student card ID(s). Existing IDs were preserved."}


@app.get("/{school_name}/admin/canteen-cards")
def list_student_canteen_card_ids(school_name: str, request: Request, db: Session = Depends(get_db), class_name: str | None = Query(None)):
    """Admin-only list for printing/issuing student canteen cards."""
    if not _check_admin(request, school_name):
        raise HTTPException(status_code=401, detail="School administrator login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    query = db.query(Student).filter(Student.school_id == school.id, Student.is_active == 1)
    if class_name and class_name.strip().lower() != "all":
        query = query.filter(Student.class_name == class_name.strip())
    rows = query.order_by(Student.class_name, Student.student_name).all()
    return {"status": "success", "students": [{"student_id": s.id, "student_name": s.student_name, "class_name": s.class_name, "card_id": s.canteen_card_id} for s in rows], "total": len(rows), "without_card_id": sum(1 for s in rows if not s.canteen_card_id)}


class EatsStudentCodePaymentInput(_PydanticModel):
    student_card_id: str = _Field(min_length=5, max_length=5, pattern=r"^[0-9]{5}$")
    item_id: int


@app.post("/{school_name}/eats/api/pay-by-student-card-id")
def eats_pay_by_student_card_id(school_name: str, payload: EatsStudentCodePaymentInput, request: Request, db: Session = Depends(get_db)):
    """Canteen-staff-only fallback payment flow while NFC hardware is not deployed."""
    if not (_check_admin(request, school_name) or _read_canteen_session(request, school_name)):
        raise HTTPException(status_code=401, detail="Canteen staff login required.")
    school = db.query(School).filter(School.school_name == school_name).first()
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    student = db.query(Student).filter(Student.school_id == school.id, Student.canteen_card_id == payload.student_card_id, Student.is_active == 1).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student card ID was not found in this school.")
    card = db.query(EatsCard).filter(EatsCard.school_id == school.id, EatsCard.student_id == student.id, EatsCard.active == 1).with_for_update().first()
    if not card:
        raise HTTPException(status_code=404, detail="This student has no active canteen balance card yet. Ask an administrator to activate it.")
    item = db.query(EatsItem).filter(EatsItem.id == payload.item_id, EatsItem.school_id == school.id, EatsItem.available == 1).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item is unavailable.")
    try:
        debit = db.execute(_sql_update(EatsCard).where(EatsCard.id == card.id, EatsCard.balance_cents >= item.price_cents, EatsCard.active == 1).values(balance_cents=EatsCard.balance_cents - item.price_cents))
        if debit.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=402, detail="Insufficient balance.")
        db.refresh(card)
        tx = EatsTransaction(school_id=school.id, student_id=student.id, card_id=card.id, item_id=item.id, amount_cents=item.price_cents, balance_after_cents=card.balance_cents, status="approved")
        db.add(tx)
        db.commit()
        return {"status": "approved", "transaction_id": tx.id, "student_id": student.id, "student_name": student.student_name, "student_card_id": student.canteen_card_id, "item": item.name, "amount_rwf": tx.amount_cents, "balance_rwf": tx.balance_after_cents}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Payment could not be completed. No approval was returned.")
