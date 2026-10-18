from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from models.inventory import Base

DATABASE_URL = "sqlite:///./tooltrack.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Crear tablas si no existen
Base.metadata.create_all(bind=engine)
