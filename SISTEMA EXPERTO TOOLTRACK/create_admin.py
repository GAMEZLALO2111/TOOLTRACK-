import getpass
import hashlib
import secrets

from models.inventory import Usuario, Base
from db import SessionLocal, engine


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def create_admin_user():
    username = input("Admin username: ").strip().lower()
    password = getpass.getpass("Admin password: ")
    if not username or not password:
        raise ValueError("Username and password are required.")

    db = SessionLocal()
    try:
        user = db.query(Usuario).filter(Usuario.username.ilike(username)).first()
        if user:
            user.password = hash_password(password)
            user.role = "admin"
            print(f"Admin privileges and password updated for '{username}'.")
        else:
            db.add(Usuario(username=username, password=hash_password(password), role="admin"))
            print(f"Admin user '{username}' created.")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    create_admin_user()
