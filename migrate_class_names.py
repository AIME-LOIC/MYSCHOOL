#!/usr/bin/env python3
"""
One-off migration: merge spaced class-name variants into the canonical no-space form.

The system previously had both 'L4 NIT' and 'L4NIT' (also 'L3 SOD' vs 'L3SOD', etc.),
so students ended up split across two classes that are really the same. This script:

  1. Moves students whose class is a spaced variant into the matching canonical class
     (e.g. 'L4 NIT' -> 'L4NIT'). If the same student name already exists in the target
     class, the spaced-variant row is deleted and visits re-linked to the kept student.
  2. Merges duplicate students left inside the same class (same school + class +
     case-insensitive name).
  3. Re-links all visits before deleting any student row.

Idempotent: safe to run any number of times. Usage:  python migrate_class_names.py
"""
import os
import re

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

CANONICAL = {"S1", "S2", "S3", "L3SOD", "L3NIT", "L4SOD", "L4NIT", "L5SOD", "L5NIT"}


def canon(c):
    token = re.sub(r"[^A-Za-z0-9]", "", str(c or "")).upper()
    return token if token in CANONICAL else None


def norm_name(n):
    return re.sub(r"\s+", " ", str(n or "")).strip().upper()


def main():
    engine = create_engine(os.getenv("DATABASE_URL"))
    with engine.begin() as conn:
        # ---- 1. Show what we're starting from ----
        print("Class-name distribution BEFORE:")
        for r in conn.execute(text(
            "SELECT class_name, COUNT(*) FROM students GROUP BY class_name ORDER BY 1"
        )).fetchall():
            print(f"  {r[0]!r}: {r[1]}")

        # ---- 2. Move spaced-variant students into their canonical class ----
        moved = merged = 0
        rows = conn.execute(text(
            "SELECT id, student_name, class_name, school_id FROM students"
        )).fetchall()

        for sid, name, cls, school_id in rows:
            canonical = canon(cls)
            if canonical is None:
                print(f"  ! leaving unknown class {cls!r} (student id={sid}) untouched")
                continue

            if cls == canonical:
                continue

            # Find an existing canonical-class student with the same name in the same school
            existing = conn.execute(text(
                """
                SELECT id FROM students
                WHERE class_name = :cls
                  AND school_id = :school_id
                  AND LOWER(TRIM(student_name)) = LOWER(TRIM(:name))
                  AND id <> :sid
                """
            ), {"cls": canonical, "school_id": school_id, "name": name, "sid": sid}).fetchone()

            if existing:
                # Keep the canonical row, delete the spaced-variant row, re-link visits
                keep_id = existing.id
                conn.execute(text("UPDATE visits SET student_id = :k WHERE student_id = :s"),
                             {"k": keep_id, "s": sid})
                conn.execute(text("DELETE FROM students WHERE id = :s"), {"s": sid})
                merged += 1
                print(f"  merged duplicate: id={sid} {name!r} ({cls!r}) -> id={keep_id} ({canonical!r})")
            else:
                conn.execute(text("UPDATE students SET class_name = :c WHERE id = :s"),
                             {"c": canonical, "s": sid})
                moved += 1
                print(f"  moved: id={sid} {name!r} {cls!r} -> {canonical!r}")

        # ---- 3. Merge same-name duplicates left inside each class ----
        dupes = conn.execute(text("""
            SELECT school_id, class_name, LOWER(TRIM(student_name)) AS nname, COUNT(*) AS cnt
            FROM students
            GROUP BY school_id, class_name, LOWER(TRIM(student_name))
            HAVING COUNT(*) > 1
        """)).fetchall()
        for school_id, cls, nname, cnt in dupes:
            keep_ids = conn.execute(text(
                """
                SELECT id FROM students
                WHERE school_id = :s AND class_name = :c AND LOWER(TRIM(student_name)) = :n
                ORDER BY id ASC
                """
            ), {"s": school_id, "c": cls, "n": nname}).fetchall()
            keep_id = keep_ids[0].id
            for (dupe_id,) in keep_ids[1:]:
                conn.execute(text("UPDATE visits SET student_id = :k WHERE student_id = :s"),
                             {"k": keep_id, "s": dupe_id})
                conn.execute(text("DELETE FROM students WHERE id = :s"), {"s": dupe_id})
                merged += 1
                print(f"  merged in-class duplicate: id={dupe_id} -> {keep_id} ({cls!r}, {nname!r})")

        # ---- 4. Show the result ----
        print()
        print("Class-name distribution AFTER:")
        for r in conn.execute(text(
            "SELECT class_name, COUNT(*) FROM students GROUP BY class_name ORDER BY 1"
        )).fetchall():
            print(f"  {r[0]!r}: {r[1]}")

    print()
    print(f"Done. moved={moved} merged={merged}")


if __name__ == "__main__":
    main()
