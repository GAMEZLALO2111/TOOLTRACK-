from models.inventory import Usuario, Base
from db import SessionLocal, engine

# Crear tablas si no existen
def create_tables():
    Base.metadata.create_all(bind=engine)

def show_users():
    db = SessionLocal()
    try:
        users = db.query(Usuario).all()
        for user in users:
            print(f"Username: {user.username}, Role: {user.role}")
    finally:
        db.close()

if __name__ == "__main__":
    create_tables()
    show_users()
