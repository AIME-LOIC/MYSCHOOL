# School Management System - System Admin Implementation

## Overview
A multi-school management system with system-wide admin dashboard for managing multiple schools.

## New Files Created

### 1. `system_admin.py`
- **School Model**: Stores school information (name, code, creation date, active status)
- **Functions**:
  - `create_school_database(school_name, school_code)` - Create new school
  - `list_all_schools()` - Get all schools
  - `delete_school_database(school_name)` - Delete school
  - `get_school_database_info(school_name)` - Get school details

### 2. `templates/system_admin.html`
- Beautiful responsive admin dashboard
- Create new schools with name and school code
- View all registered schools with details
- Delete schools with confirmation
- Quick access links to each school's admin panel
- Real-time statistics (total schools count)

## Updated Files

### 1. `config.py`
- Added support for both production (PostgreSQL) and development (SQLite) databases
- Automatically creates SQLite database `school.db` if `DATABASE_URL` env var is not set

### 2. `app.py`
**New Endpoints:**
- `GET /myadmin` - System admin dashboard
- `POST /myadmin/create-school` - Create new school
- `GET /myadmin/schools` - List all schools (JSON API)
- `DELETE /myadmin/schools/{school_id}` - Delete school

**Updated Endpoints (now school-specific):**
- `GET /{school_name}/parentportal` - Parent portal for specific school
- `GET /{school_name}/admin` - Admin dashboard for specific school
- `GET /{school_name}/students/search` - Search students
- `POST /{school_name}/visits/add` - Add visit
- `GET /{school_name}/admin/data/{visit_type}` - Get visit statistics
- `POST /{school_name}/admin/upload-students` - Upload students from Excel
- `GET /{school_name}/admin/students` - Get all students
- `DELETE /{school_name}/admin/students/{student_id}` - Delete student
- `POST /{school_name}/admin/add_student` - Add single student

### 2. `model.py`
- Added `school_id` field to Student model for tracking school association

## How It Works

### System Admin Flow
1. Navigate to `/myadmin` to access system admin dashboard
2. Enter school name and code
3. Click "Create School" - school is registered in the database
4. View all schools in the dashboard
5. Click "Admin Panel" to access school-specific admin area
6. Delete schools if needed

### School-Specific Operations
Each school has its own isolated data:
- Students are managed per school
- Visit records are school-specific
- Parent portal is school-specific

Example URLs:
- School name: "ABC High School" (school code: "ABC-001")
- Admin access: `http://localhost:5000/ABC High School/admin`
- Parent portal: `http://localhost:5000/ABC High School/parentportal`
- API: `http://localhost:5000/ABC High School/students/search`

## Database Structure

### Tables
1. **schools** - System-level schools registry
   - id (Primary Key)
   - school_name (Unique)
   - school_code (Unique)
   - created_at
   - is_active

2. **students** - Student records
   - id (Primary Key)
   - student_name
   - class_name
   - school_id (links to school)

3. **visits** - Visit/attendance records
   - id (Primary Key)
   - student_id (Foreign Key)
   - visit_type
   - visit_date
   - status

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app:app --port 5000 --reload

# Access system admin
# http://localhost:5000/myadmin
```

## Features Implemented

✅ System admin dashboard at `/myadmin`
✅ Multi-school support with separate data isolation
✅ School creation with validation
✅ School deletion with cascading data removal
✅ School-specific endpoints with dynamic routing
✅ Database auto-initialization on startup
✅ Support for both SQLite (dev) and PostgreSQL (production)
✅ Responsive UI with modern styling
✅ Real-time school statistics
✅ All original school features now per-school basis

## Next Steps (Optional)
- Add authentication/authorization
- Implement school-specific user roles
- Add more analytics and reporting
- Export data functionality
- Backup and restore features
