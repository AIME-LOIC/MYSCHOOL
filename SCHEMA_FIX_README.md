# Database Schema Fix - Summary

## Problem
The `students` table in Supabase was missing the `school_id` column, causing:
- `500 Internal Server Error` when trying to access `/students` endpoints
- Error: `column students.school_id does not exist`

## Root Cause
Old students were created without the `school_id` column because it wasn't part of the original schema. The new model definition requires this column for multi-school support.

## Solution Applied

### 1. **Updated Student Model** (`model.py`)
- Made `school_id` column nullable with a default value of 1
- This allows backwards compatibility with existing students without a school_id

### 2. **Updated Database Queries** (`app.py`)
- **search_students()**: Now searches in students with `school_id == school.id` OR `school_id IS NULL`
- **get_all_students()**: Now returns students with `school_id == school.id` OR `school_id IS NULL`

This ensures that:
- Students without a school assignment are included
- Students are still filtered by school for multi-school support

### 3. **Automatic Schema Migration** (`config.py`)
- Added `_ensure_student_school_id_column()` function
- When the app starts, it automatically:
  1. Checks if `school_id` column exists in the `students` table
  2. If not, adds the column with `DEFAULT 1`
  3. Attempts to add foreign key constraint
  4. Attempts to make the column NOT NULL after default is applied

### 4. **Migration Scripts Created**
- `migrate_add_school_id.py` - Standalone migration script (if needed)
- `check_schema.py` - Utility to inspect current database schema

## How to Use

### Option 1: Automatic (Recommended)
Just restart your app. The automatic migration in `config.py` will handle everything:
```bash
python app.py  # or uvicorn app:app --reload
```

### Option 2: Manual Migration
If automatic doesn't work, run:
```bash
python3 migrate_add_school_id.py
```

### Check Schema
To verify the schema was updated correctly:
```bash
python3 check_schema.py
```

## Expected Behavior After Fix
✅ Students table will have `school_id` column
✅ Search endpoint will return students
✅ All students endpoints will work correctly
✅ Backwards compatibility with existing students

## Next Steps (Optional)
To fully migrate existing students to assigned schools:
```sql
-- Update all NULL school_id to 1 (assuming school 1 exists)
UPDATE students SET school_id = 1 WHERE school_id IS NULL;

-- Then make the column NOT NULL
ALTER TABLE students ALTER COLUMN school_id SET NOT NULL;
```
