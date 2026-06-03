"""
Run this once to add the access control columns to your existing `schools` table.
Usage:  python migrate_schools.py
"""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL")  # same env var your app uses

engine = create_engine(DATABASE_URL)

migrations = [
    "ALTER TABLE schools ADD COLUMN IF NOT EXISTS is_open   INTEGER   DEFAULT 1",
    "ALTER TABLE schools ADD COLUMN IF NOT EXISTS open_at   TIMESTAMP DEFAULT NULL",
    "ALTER TABLE schools ADD COLUMN IF NOT EXISTS close_at  TIMESTAMP DEFAULT NULL",
]

with engine.begin() as conn:
    for sql in migrations:
        conn.execute(text(sql))
        print(f"OK  {sql}")

print("\nMigration complete.")