from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
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
    proveedor = Column(String, nullable=True)
    categoria = Column(String, nullable=True)
    estado = Column(String, nullable=False, default="disponible", server_default="disponible")
    ubicacion = Column(String, nullable=True)  # Nuevo campo para rack/ubicación
    altura_total = Column(String, nullable=True)  # Punzones: altura medida de la punta; la vida es esa altura menos el stripper
    stripper_override = Column(String, nullable=True)  # Espesor del stripper propio de esta herramienta (si no, el de su tamaño)
    imagen = Column(String, nullable=True)  # Archivo de la imagen de la herramienta
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
    imagen = Column(String, nullable=True)  # Imagen compartida por todas las herramientas de este TN


class ParametroHerramienta(Base):
    __tablename__ = "parametros_herramienta"

    clave = Column(String, primary_key=True)
    valor = Column(String, nullable=False)
    actualizado_por = Column(String, nullable=True)
    actualizado_en = Column(DateTime, nullable=True)


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
    role = Column(String, default='TOOLCRIB_OPERATOR')
    activo = Column(Boolean, nullable=False, default=True, server_default="1")
    employee_number = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    area = Column(String, nullable=True)
    shift = Column(String, nullable=True)

    @property
    def is_admin(self):
        return (self.role or '').upper() == 'SYSTEM_ADMIN'

    @is_admin.setter
    def is_admin(self, value):
        self.role = 'SYSTEM_ADMIN' if bool(value) else 'TOOLCRIB_OPERATOR'


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
    anterior_manual = Column(Boolean, nullable=False, default=False, server_default="0")


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


class SolicitudServicio(Base):
    """Service request opened from a machine position (Manufacturing -> Tool Crib)."""
    __tablename__ = "solicitudes_servicio"
    __table_args__ = (
        Index("ix_solicitudes_servicio_estado", "estado"),
        Index("ix_solicitudes_servicio_maquina", "maquina_codigo"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    setup_id = Column(Integer, ForeignKey("setups.id"), nullable=True)
    requisito_id = Column(Integer, ForeignKey("requisitos_setup.id"), nullable=True)
    numero_parte = Column(String, nullable=True)
    estacion = Column(String, nullable=True)
    categoria = Column(String, nullable=True)
    tn = Column(String, nullable=True)
    tipo = Column(String, nullable=False)
    estado = Column(String, nullable=False)
    problema = Column(String, nullable=True)
    nota = Column(Text, nullable=True)
    resolucion = Column(Text, nullable=True)
    herramienta_id = Column(String, ForeignKey("herramientas.id"), nullable=True)
    herramienta_nueva_id = Column(String, ForeignKey("herramientas.id"), nullable=True)
    destino_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=True)
    solicitado_por = Column(String, nullable=False)
    solicitado_en = Column(DateTime, nullable=False)
    atendido_por = Column(String, nullable=True)
    atendido_en = Column(DateTime, nullable=True)
    recogido_por = Column(String, nullable=True)
    recogido_en = Column(DateTime, nullable=True)
    recibida_en = Column(DateTime, nullable=True)
    demanda_id = Column(Integer, ForeignKey("demandas_planeacion.id"), nullable=True)
    cerrado_por = Column(String, nullable=True)
    cerrado_en = Column(DateTime, nullable=True)
    motivo_cancelacion = Column(Text, nullable=True)
    cambio_piloto = Column(Boolean, nullable=False, default=False, server_default="0")
    tn_instalado = Column(String, nullable=True)


class ServicioHerramienta(Base):
    """One service event on a physical tool (sharpening, repair, inspection): the history behind damage Paretos."""
    __tablename__ = "servicios_herramienta"
    __table_args__ = (
        Index("ix_servicios_herramienta_tool", "herramienta_id"),
        Index("ix_servicios_herramienta_fecha", "fecha"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    herramienta_id = Column(String, ForeignKey("herramientas.id"), nullable=False)
    solicitud_id = Column(Integer, ForeignKey("solicitudes_servicio.id"), nullable=True)
    tipo = Column(String, nullable=False)
    resultado = Column(String, nullable=False)
    maquina_codigo = Column(String, nullable=True)
    numero_parte = Column(String, nullable=True)
    estacion = Column(String, nullable=True)
    tn = Column(String, nullable=True)
    categoria = Column(String, nullable=True)
    rebaje = Column(Float, nullable=True)
    life_antes = Column(String, nullable=True)
    life_despues = Column(String, nullable=True)
    anormal = Column(Boolean, nullable=False, default=False, server_default="0")
    justificacion = Column(Text, nullable=True)
    nota = Column(Text, nullable=True)
    realizado_por = Column(String, nullable=False)
    fecha = Column(DateTime, nullable=False)


class IncidenteHerramienta(Base):
    """What happened to a tool (damage or escalation): the record behind damage Paretos.

    Created when an operator reports damage, or when Tool Crib finds a tool needs more than the
    reported service. `reportado_como` keeps what Manufacturing reported; `hallazgo` what Tool Crib found.
    """
    __tablename__ = "incidentes_herramienta"
    __table_args__ = (
        UniqueConstraint("solicitud_id", name="uq_incidente_solicitud"),
        Index("ix_incidentes_herramienta_tool", "herramienta_id"),
        Index("ix_incidentes_herramienta_fecha", "creado_en"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    herramienta_id = Column(String, ForeignKey("herramientas.id"), nullable=False)
    solicitud_id = Column(Integer, ForeignKey("solicitudes_servicio.id"), nullable=True)
    ticket_id = Column(Integer, nullable=True)
    origen = Column(String, nullable=False)
    reportado_como = Column(String, nullable=True)
    maquina_codigo = Column(String, nullable=True)
    numero_parte = Column(String, nullable=True)
    estacion = Column(String, nullable=True)
    setup_id = Column(Integer, nullable=True)
    tn = Column(String, nullable=True)
    categoria = Column(String, nullable=True)
    life = Column(String, nullable=True)
    operador = Column(String, nullable=True)
    turno = Column(String, nullable=True)
    causa = Column(String, nullable=True)
    momento = Column(String, nullable=True)
    descripcion = Column(Text, nullable=True)
    hallazgo = Column(Text, nullable=True)
    creado_por = Column(String, nullable=False)
    creado_en = Column(DateTime, nullable=False)
    actualizado_por = Column(String, nullable=True)
    actualizado_en = Column(DateTime, nullable=True)


class TicketMantenimiento(Base):
    """Maintenance ticket: always opened by Tool Crib, worked by the maintenance technician.

    open -> evaluating (technician received the tool) -> repairing (repairable) -> closed (released),
    or closed as unrepairable. The incident record keeps what happened; this keeps the repair work.
    """
    __tablename__ = "tickets_mantenimiento"
    __table_args__ = (
        Index("ix_tickets_mantenimiento_tool", "herramienta_id"),
        Index("ix_tickets_mantenimiento_estado", "estado"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    herramienta_id = Column(String, ForeignKey("herramientas.id"), nullable=False)
    solicitud_id = Column(Integer, ForeignKey("solicitudes_servicio.id"), nullable=True)
    incidente_id = Column(Integer, ForeignKey("incidentes_herramienta.id"), nullable=False)
    estado = Column(String, nullable=False, default="open")
    motivo = Column(Text, nullable=False)
    creado_por = Column(String, nullable=False)
    creado_en = Column(DateTime, nullable=False)
    recibido_por = Column(String, nullable=True)
    recibido_en = Column(DateTime, nullable=True)
    diagnostico = Column(Text, nullable=True)
    reparable = Column(Boolean, nullable=True)
    causa_tecnica = Column(String, nullable=True)
    evaluado_por = Column(String, nullable=True)
    evaluado_en = Column(DateTime, nullable=True)
    trabajo = Column(Text, nullable=True)
    resultado = Column(String, nullable=True)
    cerrado_por = Column(String, nullable=True)
    cerrado_en = Column(DateTime, nullable=True)


class BajaHerramienta(Base):
    """Retirement (baja) of a physical tool: proposed by Tool Crib with the incident behind it,
    approved or rejected by a supervisor or the maintenance technician (never by the proposer)."""
    __tablename__ = "bajas_herramienta"
    __table_args__ = (
        Index("ix_bajas_herramienta_tool", "herramienta_id"),
        Index("ix_bajas_herramienta_estado", "estado"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    herramienta_id = Column(String, ForeignKey("herramientas.id"), nullable=False)
    solicitud_id = Column(Integer, ForeignKey("solicitudes_servicio.id"), nullable=True)
    incidente_id = Column(Integer, ForeignKey("incidentes_herramienta.id"), nullable=False)
    estado = Column(String, nullable=False, default="pending")
    estado_previo = Column(String, nullable=True)
    motivo = Column(Text, nullable=False)
    propuesto_por = Column(String, nullable=False)
    propuesto_en = Column(DateTime, nullable=False)
    resuelto_por = Column(String, nullable=True)
    resuelto_en = Column(DateTime, nullable=True)
    nota_resolucion = Column(Text, nullable=True)


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
    # How the tool code was entered: "scan" or "manual" (NULL for movements recorded before this existed).
    captura = Column(String, nullable=True)


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
    deshabilitada = Column(Boolean, nullable=False, default=False, server_default="0")
    deshab_tipo = Column(String, nullable=True)
    deshab_motivo = Column(Text, nullable=True)
    deshab_desde = Column(DateTime, nullable=True)
    deshab_por = Column(String, nullable=True)
    regreso_estimado = Column(Date, nullable=True)


class MaquinaDisponibilidad(Base):
    """History of a machine being disabled and enabled, with the reason."""
    __tablename__ = "maquina_disponibilidad"
    __table_args__ = (Index("ix_maquina_disponibilidad_maquina", "maquina_codigo"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    accion = Column(String, nullable=False)
    tipo = Column(String, nullable=True)
    motivo = Column(Text, nullable=True)
    regreso_estimado = Column(Date, nullable=True)
    usuario = Column(String, nullable=False)
    fecha = Column(DateTime, nullable=False)


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
    piezas_blank = Column(Integer, nullable=True)
    cycle_time_s = Column(Integer, nullable=True)
    cycle_con_lead_s = Column(Integer, nullable=True)
    uph_teorico = Column(Float, nullable=True)
    uph_editado = Column(Float, nullable=True)
    uph_motivo = Column(Text, nullable=True)
    uph_editado_por = Column(String, nullable=True)
    uph_editado_en = Column(DateTime, nullable=True)


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


class DemandaPlaneacion(Base):
    """Cantidad planeada de un número de parte en una máquina (tablero de secuencia)."""
    __tablename__ = "demandas_planeacion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    numero_parte = Column(String, nullable=False)
    cantidad = Column(Integer, nullable=False)
    piezas_producidas = Column(Integer, nullable=False, default=0, server_default="0")
    orden = Column(Integer, nullable=False, default=0, server_default="0")
    marca = Column(String, nullable=True)
    comentario = Column(Text, nullable=True)
    activa = Column(Boolean, nullable=False, default=True, server_default="1")
    orden_trabajo = Column(String, nullable=True)
    creado_por = Column(String, nullable=False)
    creado_en = Column(DateTime, nullable=False)
    actualizado_en = Column(DateTime, nullable=False)
    estado_cierre = Column(String, nullable=True)
    motivo_cierre = Column(Text, nullable=True)
    cerrado_por = Column(String, nullable=True)
    cerrado_en = Column(DateTime, nullable=True)


class RegistroProduccion(Base):
    """Append-only production log. It outlives the demand: removing a demand from the Board never removes it.

    `fuente` says where it came from (manual today; baan or mespro later) and `id_externo` keeps an
    imported record from being counted twice. A negative `piezas` is a correction.
    """
    __tablename__ = "registros_produccion"
    __table_args__ = (
        UniqueConstraint("fuente", "id_externo", name="uq_produccion_externo"),
        Index("ix_produccion_parte_fecha", "numero_parte", "fecha_produccion"),
        Index("ix_produccion_demanda", "demanda_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    demanda_id = Column(Integer, ForeignKey("demandas_planeacion.id"), nullable=True)
    numero_parte = Column(String, nullable=False)
    maquina_codigo = Column(String, nullable=True)
    piezas = Column(Integer, nullable=False)
    fecha_produccion = Column(DateTime, nullable=False)
    fecha_estimada = Column(Boolean, nullable=False, default=False, server_default="0")
    turno = Column(String, nullable=True)
    fuente = Column(String, nullable=False, default="manual", server_default="manual")
    id_externo = Column(String, nullable=True)
    capturado_por = Column(String, nullable=False)
    capturado_en = Column(DateTime, nullable=False)
    nota = Column(Text, nullable=True)


class AsignacionMaquina(Base):
    """Who is working on a machine: the operator logs into the machine each time, until the shift ends."""
    __tablename__ = "asignaciones_maquina"
    __table_args__ = (
        Index("ix_asignaciones_maquina_usuario", "usuario"),
        Index("ix_asignaciones_maquina_maquina", "maquina_codigo"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    usuario = Column(String, nullable=False)
    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), nullable=False)
    turno = Column(String, nullable=False)
    inicio = Column(DateTime, nullable=False)
    vence = Column(DateTime, nullable=False)
    fin = Column(DateTime, nullable=True)
    fin_motivo = Column(String, nullable=True)


class Sesion(Base):
    """Token de sesión (solo se guarda el hash SHA-256)."""
    __tablename__ = "sesiones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String, nullable=False, unique=True)
    username = Column(String, nullable=False, index=True)
    creado_en = Column(DateTime, nullable=False)
    expira_en = Column(DateTime, nullable=False)
    revocada = Column(Boolean, nullable=False, default=False, server_default="0")


class Auditoria(Base):
    """Bitácora de cambios: quién, cuándo, qué acción y valores anteriores/nuevos."""
    __tablename__ = "auditoria"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fecha = Column(DateTime, nullable=False, index=True)
    usuario = Column(String, nullable=False, index=True)
    rol = Column(String, nullable=True)
    accion = Column(String, nullable=False)
    entidad = Column(String, nullable=True)
    entidad_id = Column(String, nullable=True)
    antes = Column(Text, nullable=True)
    despues = Column(Text, nullable=True)


class FgPlaneacion(Base):
    """Finish good (a rack, a shelf…) planned from its make items. Several FG numbers may share one BOM."""
    __tablename__ = "fg_planeacion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    numero_fg = Column(String, nullable=False, unique=True)
    descripcion = Column(String, nullable=True)
    cliente = Column(String, nullable=True)
    bom_de_id = Column(Integer, ForeignKey("fg_planeacion.id"), nullable=True)
    activa = Column(Boolean, nullable=False, default=True, server_default="1")
    creado_por = Column(String, nullable=False)
    creado_en = Column(DateTime, nullable=False)
    actualizado_en = Column(DateTime, nullable=False)


class FgParte(Base):
    """One make item of the BOM of a FG and how many pieces of it each FG unit needs."""
    __tablename__ = "fg_partes"
    __table_args__ = (UniqueConstraint("fg_id", "numero_parte", name="uq_fg_parte"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    fg_id = Column(Integer, ForeignKey("fg_planeacion.id"), nullable=False)
    numero_parte = Column(String, nullable=False)
    piezas_por_fg = Column(Integer, nullable=False)


class ForecastSemana(Base):
    """Weekly demand of a FG (week = its Monday)."""
    __tablename__ = "forecast_semanas"
    __table_args__ = (UniqueConstraint("fg_id", "semana", name="uq_forecast_fg_semana"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    fg_id = Column(Integer, ForeignKey("fg_planeacion.id"), nullable=False)
    semana = Column(Date, nullable=False)
    cantidad = Column(Integer, nullable=False)
    actualizado_por = Column(String, nullable=False)
    actualizado_en = Column(DateTime, nullable=False)


class CapacidadMaquina(Base):
    """Weekly capacity of a machine: shifts × hours × days × efficiency. A machine without a row uses the defaults."""
    __tablename__ = "capacidad_maquina"

    maquina_codigo = Column(String, ForeignKey("ubicaciones.codigo"), primary_key=True)
    turnos = Column(Integer, nullable=False)
    horas_turno = Column(Float, nullable=False)
    dias_semana = Column(Float, nullable=False)
    eficiencia_pct = Column(Float, nullable=False)
    incluida = Column(Boolean, nullable=False, default=True, server_default="1")
    actualizado_por = Column(String, nullable=True)
    actualizado_en = Column(DateTime, nullable=True)


class ParametroPlaneacion(Base):
    __tablename__ = "parametros_planeacion"

    clave = Column(String, primary_key=True)
    valor = Column(String, nullable=False)
    actualizado_por = Column(String, nullable=True)
    actualizado_en = Column(DateTime, nullable=True)
