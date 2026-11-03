import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from models.inventory import Base

# Ruta absoluta: arrancar el backend desde otra carpeta no debe crear una base vacía.
DB_PATH = os.getenv("TOOLTRACK_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tooltrack.db"
)
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 15})


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Crear tablas si no existen
Base.metadata.create_all(bind=engine)
