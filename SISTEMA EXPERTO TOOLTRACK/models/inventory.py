from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Herramienta(Base):
    __tablename__ = 'herramientas'
    id = Column(String, primary_key=True)  # Ahora el ID es el campo de usuario, no autoincremental
    tn = Column(String, nullable=True)
    size = Column(String, nullable=True)  # Antes 'type', ahora 'size' (A, B, C, D, E)
    type = Column(String, nullable=True)  # Nuevo campo 'type' (ROUND, SQUARE, etc)
    life = Column(String, nullable=True)
    clearance = Column(String, nullable=True)  # Nuevo campo para matriz
    so = Column(String, nullable=True)
    categoria = Column(String, nullable=True)
    ubicacion = Column(String, nullable=True)  # Nuevo campo para rack/ubicación

    def __repr__(self):
        return f"<Herramienta(id={self.id}, tn={self.tn}, size={self.size}, type={self.type}, life={self.life}, clearance={self.clearance}, so={self.so}, categoria={self.categoria}, ubicacion={self.ubicacion})>"

class Usuario(Base):
    __tablename__ = 'usuarios'
    username = Column(String, primary_key=True)
    password = Column(String, nullable=False)
    role = Column(String, default='operator')  # admin, supervisor, operator

    @property
    def is_admin(self):
        return (self.role or '').lower() == 'admin'

    @is_admin.setter
    def is_admin(self, value):
        self.role = 'admin' if bool(value) else 'operator'
