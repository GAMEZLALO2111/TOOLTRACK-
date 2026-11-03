import getpass

from models.inventory import Usuario, Base
from db import SessionLocal, engine
from passwords import hash_password


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
            user.role = "SYSTEM_ADMIN"
            user.activo = True
            print(f"System admin privileges and password updated for '{username}'.")
        else:
            db.add(Usuario(username=username, password=hash_password(password), role="SYSTEM_ADMIN", activo=True))
            print(f"System admin user '{username}' created.")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    create_admin_user()
