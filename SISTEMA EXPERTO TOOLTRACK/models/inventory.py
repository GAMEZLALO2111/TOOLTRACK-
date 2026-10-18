from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

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
    estado = Column(String, nullable=False, default="disponible", server_default="disponible")
    ubicacion = Column(String, nullable=True)  # Nuevo campo para rack/ubicación
    catalogo = relationship(
        "HerramientaCatalogo",
        back_populates="herramienta",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self):
        return f"<Herramienta(id={self.id}, tn={self.tn}, size={self.size}, type={self.type}, life={self.life}, clearance={self.clearance}, so={self.so}, categoria={self.categoria}, ubicacion={self.ubicacion})>"


class NumeroHerramienta(Base):
    __tablename__ = "numeros_herramienta"

    tn = Column(String, primary_key=True)


class ConfiguracionTN(Base):
    __tablename__ = "configuraciones_tn"
    __table_args__ = (
        ForeignKeyConstraint(["tn"], ["numeros_herramienta.tn"]),
    )

    tn = Column(String, primary_key=True)
    size = Column(String, primary_key=True)
    type = Column(String, nullable=False)
    variantes = relationship(
        "VarianteHerramienta",
        back_populates="configuracion",
        cascade="all, delete-orphan",
    )


class VarianteHerramienta(Base):
    __tablename__ = "variantes_herramienta"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tn", "size"],
            ["configuraciones_tn.tn", "configuraciones_tn.size"],
        ),
        CheckConstraint(
            "(categoria = 'punzon' AND clearance IS NULL) OR "
            "(categoria = 'matriz' AND clearance IS NOT NULL)",
            name="ck_variante_categoria_clearance",
        ),
        UniqueConstraint("tn", "size", "categoria", "clearance"),
        Index(
            "uq_variante_punzon_configuracion",
            "tn",
            "size",
            unique=True,
            sqlite_where=(Column("categoria") == "punzon"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tn = Column(String, nullable=False)
    size = Column(String, nullable=False)
    categoria = Column(String, nullable=False)
    clearance = Column(String, nullable=True)
    configuracion = relationship("ConfiguracionTN", back_populates="variantes")
    herramientas_fisicas = relationship(
        "HerramientaCatalogo",
        back_populates="variante",
        cascade="all, delete-orphan",
    )


class HerramientaCatalogo(Base):
    __tablename__ = "herramientas_catalogo"

    herramienta_id = Column(
        String,
        ForeignKey("herramientas.id"),
        primary_key=True,
    )
    variante_id = Column(
        Integer,
        ForeignKey("variantes_herramienta.id"),
        nullable=False,
    )
    herramienta = relationship("Herramienta", back_populates="catalogo")
    variante = relationship("VarianteHerramienta", back_populates="herramientas_fisicas")


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


class Ubicacion(Base):
    __tablename__ = "ubicaciones"

    codigo = Column(String, primary_key=True)
    nombre = Column(String, nullable=False)
    tipo = Column(String, nullable=False)
    activa = Column(Boolean, nullable=False, default=True)
    # Standard machine (turret): its tools are reused, so pickups are not "overdue".
    estandar = Column(Boolean, nullable=False, default=False, server_default="0")


class Setup(Base):
    __tablename__ = "setups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    nombre_archivo = Column(String, nullable=False)
    drawing_name = Column(String, nullable=True)
    material = Column(String, nullable=True)
    thickness = Column(String, nullable=False)
    contenido_hash = Column(String, nullable=False)
    creado_por = Column(String, nullable=False)
    creado_en = Column(DateTime, nullable=False)
    estado = Column(String, nullable=False)


class EstadoMaquina(Base):
    __tablename__ = "estados_maquina"

    maquina_codigo = Column(
        String,
        ForeignKey("ubicaciones.codigo"),
        primary_key=True,
    )
    setup_activo_id = Column(Integer, ForeignKey("setups.id"), nullable=True)


class RequisitoSetup(Base):
    __tablename__ = "requisitos_setup"

    id = Column(Integer, primary_key=True, autoincrement=True)
    setup_id = Column(Integer, ForeignKey("setups.id"), nullable=False)
    station = Column(String, nullable=False)
    tn = Column(String, nullable=False)
    angle = Column(String, nullable=True)
    punch_tool_id = Column(String, ForeignKey("herramientas.id"), nullable=True)
    die_tool_id = Column(String, ForeignKey("herramientas.id"), nullable=True)


class Surtido(Base):
    __tablename__ = "surtidos"

    id = Column(String, primary_key=True)
    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    carro_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    setup_anterior_id = Column(Integer, ForeignKey("setups.id"), nullable=False)
    setup_nuevo_id = Column(Integer, ForeignKey("setups.id"), nullable=False)
    estado = Column(String, nullable=False)
    qr_token = Column(String, nullable=True, unique=True)
    creado_por = Column(String, nullable=False)
    creado_en = Column(DateTime, nullable=False)
    actualizado_en = Column(DateTime, nullable=False)
    entregado_en = Column(DateTime, nullable=True)
    motivo = Column(String, nullable=True)


class ComponenteSurtido(Base):
    __tablename__ = "componentes_surtido"

    id = Column(Integer, primary_key=True, autoincrement=True)
    surtido_id = Column(String, ForeignKey("surtidos.id"), nullable=False)
    requisito_id = Column(Integer, ForeignKey("requisitos_setup.id"), nullable=False)
    categoria = Column(String, nullable=False)
    accion = Column(String, nullable=False)
    herramienta_anterior_id = Column(String, ForeignKey("herramientas.id"), nullable=True)
    herramienta_nueva_id = Column(String, ForeignKey("herramientas.id"), nullable=True)
    origen_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=True)
    estado = Column(String, nullable=False)
    __table_args__ = (
        UniqueConstraint("surtido_id", "requisito_id", "categoria"),
    )


class ReservaHerramienta(Base):
    __tablename__ = "reservas_herramienta"

    herramienta_id = Column(
        String,
        ForeignKey("herramientas.id"),
        primary_key=True,
    )
    surtido_id = Column(String, ForeignKey("surtidos.id"), nullable=False)
    componente_id = Column(Integer, ForeignKey("componentes_surtido.id"), nullable=False)


class PendienteRecoger(Base):
    __tablename__ = "pendientes_recoger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    herramienta_id = Column(String, ForeignKey("herramientas.id"), nullable=False)
    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    setup_origen_id = Column(Integer, ForeignKey("setups.id"), nullable=False)
    surtido_id = Column(String, ForeignKey("surtidos.id"), nullable=False)
    creado_en = Column(DateTime, nullable=False)
    recogido_en = Column(DateTime, nullable=True)
    estado = Column(String, nullable=False)


class MovimientoHerramienta(Base):
    __tablename__ = "movimientos_herramienta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    herramienta_id = Column(String, ForeignKey("herramientas.id"), nullable=False)
    origen_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=True)
    destino_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    tipo = Column(String, nullable=False)
    surtido_id = Column(String, ForeignKey("surtidos.id"), nullable=True)
    setup_id = Column(Integer, ForeignKey("setups.id"), nullable=True)
    realizado_por = Column(String, nullable=False)
    creado_en = Column(DateTime, nullable=False)
    detalle = Column(Text, nullable=True)


class Maquina(Base):
    """Machine profile: extra data for a location of type 'machine'."""
    __tablename__ = "maquinas"

    codigo = Column(String, ForeignKey("ubicaciones.codigo"), primary_key=True)
    tipo_maquina = Column(String, nullable=True)
    linea = Column(String, nullable=True)
    capacidad = Column(String, nullable=True)
    espesor_max = Column(String, nullable=True)
    espesor_min = Column(String, nullable=True)
    edificio = Column(String, nullable=True)
    blank_max = Column(String, nullable=True)
    area_pallet = Column(String, nullable=True)
    notas = Column(Text, nullable=True)


class EstacionMaquina(Base):
    __tablename__ = "estaciones_maquina"
    __table_args__ = (UniqueConstraint("maquina_codigo", "numero", name="uq_estacion_maquina"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    numero = Column(String, nullable=False)
    tamano = Column(String, nullable=True)
    posicion = Column(String, nullable=True)
    autoindex = Column(Boolean, nullable=False, default=False, server_default="0")
    estado = Column(String, nullable=False, default="disponible", server_default="disponible")
    nota = Column(Text, nullable=True)


class Cliente(Base):
    __tablename__ = "clientes"

    nombre = Column(String, primary_key=True)


class FamiliaParte(Base):
    __tablename__ = "familias_parte"

    nombre = Column(String, primary_key=True)
    cliente = Column(String, nullable=True)


class ProgramaParte(Base):
    """Official program of a part number for one machine type (4I, 2I, 45STA)."""
    __tablename__ = "programas_parte"
    __table_args__ = (UniqueConstraint("numero_parte", "tipo_maquina", name="uq_programa_parte"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    numero_parte = Column(String, nullable=False)
    tipo_maquina = Column(String, nullable=False)
    familia = Column(String, nullable=True)
    tipo_programa = Column(String, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    nombre_archivo = Column(String, nullable=True)
    drawing_name = Column(String, nullable=True)
    material = Column(String, nullable=True)
    thickness = Column(String, nullable=True)
    contenido = Column(Text, nullable=False)
    contenido_hash = Column(String, nullable=False)
    requisitos_json = Column(Text, nullable=False)
    actualizado_por = Column(String, nullable=False)
    actualizado_en = Column(DateTime, nullable=False)


class BitacoraPrograma(Base):
    __tablename__ = "bitacora_programas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    numero_parte = Column(String, nullable=False)
    tipo_maquina = Column(String, nullable=False)
    accion = Column(String, nullable=False)
    revision = Column(Integer, nullable=False)
    motivo = Column(Text, nullable=False)
    usuario = Column(String, nullable=False)
    fecha = Column(DateTime, nullable=False)
    detalle = Column(Text, nullable=True)


class EventoMaquina(Base):
    """Audit trail of which part number runs on which machine (transfers, corrections)."""
    __tablename__ = "eventos_maquina"

    id = Column(Integer, primary_key=True, autoincrement=True)
    maquina_codigo = Column(String, nullable=False)
    tipo = Column(String, nullable=False)
    detalle = Column(Text, nullable=False)
    realizado_por = Column(String, nullable=False)
    creado_en = Column(DateTime, nullable=False)
