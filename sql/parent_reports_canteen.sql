-- MYSCHOOL: parent accounts, student marks, and dedicated canteen staff.
-- Run in the Supabase SQL Editor. Safe to rerun.

ALTER TABLE students
    ADD COLUMN IF NOT EXISTS parent_link_code VARCHAR(32);

CREATE TABLE IF NOT EXISTS parent_accounts (
    id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    parent_name VARCHAR(120) NOT NULL,
    email VARCHAR(254) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_parent_school_email UNIQUE (school_id, email)
);

CREATE TABLE IF NOT EXISTS student_marks (
    id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject VARCHAR(100) NOT NULL,
    term VARCHAR(40) NOT NULL,
    academic_year VARCHAR(20) NOT NULL,
    score INTEGER NOT NULL CHECK (score >= 0),
    max_score INTEGER NOT NULL DEFAULT 100 CHECK (max_score > 0),
    teacher_comment VARCHAR(1000),
    published INTEGER NOT NULL DEFAULT 0 CHECK (published IN (0, 1)),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_student_mark_score_range CHECK (score <= max_score)
);

CREATE TABLE IF NOT EXISTS canteen_admins (
    id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    username VARCHAR(80) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_canteen_admin_school_username UNIQUE (school_id, username)
);

CREATE INDEX IF NOT EXISTS ix_parent_accounts_student
    ON parent_accounts (school_id, student_id);
CREATE INDEX IF NOT EXISTS ix_student_marks_report
    ON student_marks (school_id, student_id, published, academic_year, term);
CREATE INDEX IF NOT EXISTS ix_canteen_admins_school
    ON canteen_admins (school_id, username);

-- Issue a school-controlled parent linking code only for students that do not
-- already have one. Distribute these privately to verified parents/guardians.
UPDATE students
SET parent_link_code = UPPER(SUBSTRING(MD5(RANDOM()::TEXT || CLOCK_TIMESTAMP()::TEXT || id::TEXT), 1, 10))
WHERE parent_link_code IS NULL OR BTRIM(parent_link_code) = '';

-- Run separately when you need the list of codes to distribute securely:
-- SELECT id AS student_id, student_name, class_name, parent_link_code
-- FROM students
-- WHERE parent_link_code IS NOT NULL
-- ORDER BY class_name, student_name;
