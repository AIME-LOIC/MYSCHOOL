# create_tables.py
from config import Base
from model import Student, Visit
from config import engine  # your SQLAlchemy engine from db.py

# Create tables
Base.metadata.create_all(bind=engine)
print("Tables created successfully!")
