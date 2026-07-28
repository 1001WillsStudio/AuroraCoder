import os
from sqlmodel import Session, select
from app.database import engine, create_db_and_tables
from app.models import User
from app.auth import get_password_hash

def init_db():
    create_db_and_tables()
    
    with Session(engine) as session:
        # Create System User
        system_user = session.exec(select(User).where(User.username == "system")).first()
        if not system_user:
            print("Creating 'system' user...")
            system_user = User(
                username="system",
                email="admin@toolstore.local",
                password_hash=get_password_hash(os.environ.get("TOOLSTORE_SYSTEM_PASSWORD", "system_password_change_me"))
            )
            session.add(system_user)
            session.commit()
            session.refresh(system_user)
            print("System user created.")
        else:
            print("System user already exists.")

if __name__ == "__main__":
    init_db()

