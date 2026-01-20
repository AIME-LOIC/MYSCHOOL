#!/usr/bin/env python3
"""
Check the current database schema
"""
from sqlalchemy import inspect
from config import engine

def check_schema():
    """Inspect and display database schema"""
    inspector = inspect(engine)
    
    print("=== Database Schema ===\n")
    
    tables = inspector.get_table_names()
    print(f"Tables found: {tables}\n")
    
    for table_name in tables:
        print(f"Table: {table_name}")
        columns = inspector.get_columns(table_name)
        for col in columns:
            print(f"  - {col['name']}: {col['type']} (nullable: {col['nullable']})")
        
        # Check foreign keys
        fks = inspector.get_foreign_keys(table_name)
        if fks:
            print("  Foreign Keys:")
            for fk in fks:
                print(f"    - {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")
        print()

if __name__ == "__main__":
    try:
        check_schema()
    except Exception as e:
        print(f"Error: {e}")
