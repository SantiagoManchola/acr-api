"""Modelos SQLAlchemy para las 16 tablas de ACR (mysql+pymysql).

Tipos/ENUMs y FKs respetan `datamodel/migrations/acr_migrations.sql`.
Incluye trazabilidad (created_by/updated_by, created_at/updated_at) y soft
delete con `estado` en las tablas transaccionales.
"""
import enum

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from .db import Base


# ----------------------------- Enums -----------------------------------------
class EstadoRegistro(str, enum.Enum):
    activo = "activo"
    inactivo = "inactivo"


class TipoAgua(str, enum.Enum):
    cruda = "cruda"
    tratada = "tratada"


class TipoUsuario(str, enum.Enum):
    residencial = "residencial"
    comercial = "comercial"
    otro = "otro"
    oficial = "oficial"


class TipoMovimiento(str, enum.Enum):
    entrada = "entrada"
    salida = "salida"


class CondicionMedidor(str, enum.Enum):
    """Condición operativa del medidor: bueno (normal), defectuoso (se marca,
    se fija manualmente) y frenado (contador detenido, se detecta solo)."""
    bueno = "bueno"
    defectuoso = "defectuoso"
    frenado = "frenado"


class CategoriaTipo(str, enum.Enum):
    equipo = "equipo"
    herramienta = "herramienta"
    laboratorio = "laboratorio"
    accesorio = "accesorio"
    insumo = "insumo"


def _estado():
    return Enum(EstadoRegistro, name="estado_registro", native_enum=True)


# ----------------------------- Auth / admin ----------------------------------
class Rol(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(50), nullable=False, unique=True)
    descripcion = Column(Text())
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    usuarios = relationship("Usuario", back_populates="rol")


class Permiso(Base):
    __tablename__ = "permisos"

    id = Column(Integer, primary_key=True)
    codigo = Column(String(50), nullable=False, unique=True)
    descripcion = Column(Text())
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class RolPermiso(Base):
    __tablename__ = "rol_permisos"

    rol_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permiso_id = Column(Integer, ForeignKey("permisos.id", ondelete="CASCADE"), primary_key=True)


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False)
    identificacion = Column(String(30))
    username = Column(String(50), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    rol_id = Column(Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)
    estado = Column(_estado(), nullable=False, server_default=EstadoRegistro.activo.value)
    ultimo_acceso = Column(DateTime())
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    rol = relationship("Rol", back_populates="usuarios")


# ----------------------------- Inventario ------------------------------------
class CategoriaInventario(Base):
    __tablename__ = "categorias_inventario"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100), nullable=False)
    tipo = Column(Enum(CategoriaTipo, name="categoria_tipo", native_enum=True), nullable=False)
    descripcion = Column(Text())
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class ElementoInventario(Base):
    __tablename__ = "elementos_inventario"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False)
    categoria_id = Column(Integer, ForeignKey("categorias_inventario.id", ondelete="RESTRICT"), nullable=False)
    unidad = Column(String(20))
    proveedor = Column(String(150))
    valor = Column(Numeric(14, 2))
    minimo = Column(Numeric(12, 2))
    estado = Column(_estado(), nullable=False, server_default=EstadoRegistro.activo.value)
    observaciones = Column(Text())
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class StockUbicacion(Base):
    """Existencias de un mismo producto en una ubicación concreta.

    Permite que un producto esté en N ubicaciones sin duplicar la ficha del
    elemento: se traslada stock del mismo producto entre ubicaciones.
    """
    __tablename__ = "stock_ubicacion"

    id = Column(Integer, primary_key=True)
    elemento_id = Column(Integer, ForeignKey("elementos_inventario.id", ondelete="CASCADE"), nullable=False)
    ubicacion_id = Column(Integer, ForeignKey("ubicaciones.id", ondelete="RESTRICT"), nullable=False)
    cantidad = Column(Numeric(12, 2), nullable=False, server_default="0")
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("elemento_id", "ubicacion_id", name="uq_stock_elem_ubi"),
    )


class MovimientoInventario(Base):
    __tablename__ = "movimientos_inventario"

    id = Column(Integer, primary_key=True)
    elemento_id = Column(Integer, ForeignKey("elementos_inventario.id", ondelete="RESTRICT"), nullable=False)
    ubicacion_id = Column(Integer, ForeignKey("ubicaciones.id", ondelete="RESTRICT"), nullable=False)
    tipo = Column(Enum(TipoMovimiento, name="tipo_movimiento", native_enum=True), nullable=False)
    cantidad = Column(Numeric(12, 2), nullable=False)
    responsable_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    motivo = Column(String(150))
    observaciones = Column(Text())
    fecha = Column(Date, nullable=False)
    hora = Column(Time)
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Ubicacion(Base):
    __tablename__ = "ubicaciones"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100), nullable=False, unique=True)
    descripcion = Column(Text())
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Traslado(Base):
    """Mueve stock del MISMO producto entre dos ubicaciones."""
    __tablename__ = "traslados"

    id = Column(Integer, primary_key=True)
    elemento_id = Column(Integer, ForeignKey("elementos_inventario.id", ondelete="RESTRICT"), nullable=False)
    ubicacion_origen_id = Column(Integer, ForeignKey("ubicaciones.id", ondelete="RESTRICT"), nullable=False)
    ubicacion_destino_id = Column(Integer, ForeignKey("ubicaciones.id", ondelete="RESTRICT"), nullable=False)
    cantidad = Column(Numeric(12, 2), nullable=False)
    responsable_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    observaciones = Column(Text())
    fecha = Column(Date, nullable=False)
    hora = Column(Time)
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


# ----------------------------- Micromedidores --------------------------------
class Suscriptor(Base):
    __tablename__ = "suscriptores"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False)
    identificacion = Column(String(30))
    codigo_usuario = Column(String(30), unique=True)
    codigo_facturacion = Column(String(30))
    tipo_usuario = Column(Enum(TipoUsuario, name="tipo_usuario", native_enum=True), nullable=False, server_default=TipoUsuario.residencial.value)
    sector = Column(String(80))
    direccion = Column(String(200))
    estado = Column(_estado(), nullable=False, server_default=EstadoRegistro.activo.value)
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Sector(Base):
    """Catálogo administrable de sectores/barrios (clasificación de suscriptores).

    Solo el admin crea/edita/inactiva. Inactivar no borra el historial: los
    suscriptores conservan el texto, pero ya no es asignable a nuevos.
    """
    __tablename__ = "sectores"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False, unique=True)
    estado = Column(_estado(), nullable=False, server_default=EstadoRegistro.activo.value)
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Micromedidor(Base):
    __tablename__ = "micromedidores"
    id = Column(Integer, primary_key=True)
    serial = Column(String(50), nullable=False, unique=True)
    tipo = Column(String(50))
    suscriptor_id = Column(Integer, ForeignKey("suscriptores.id", ondelete="RESTRICT"))
    direccion = Column(String(200))
    fecha_instalacion = Column(Date)
    condicion = Column(
        Enum(CondicionMedidor, name="condicion_medidor", native_enum=True),
        nullable=False,
        server_default=CondicionMedidor.bueno.value,
    )
    estado = Column(_estado(), nullable=False, server_default=EstadoRegistro.activo.value)
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Lectura(Base):
    __tablename__ = "lecturas"

    id = Column(Integer, primary_key=True)
    micromedidor_id = Column(Integer, ForeignKey("micromedidores.id", ondelete="RESTRICT"), nullable=False)
    suscriptor_id = Column(Integer, ForeignKey("suscriptores.id", ondelete="RESTRICT"), nullable=False)
    fecha = Column(Date, nullable=False)
    hora = Column(Time)
    lectura = Column(Numeric(12, 3), nullable=False)
    consumo = Column(Numeric(12, 3))
    promedio_usado = Column(Boolean, nullable=False, server_default="0")
    responsable_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    novedad = Column(String(200))
    irregular = Column(Boolean, nullable=False, server_default="0")
    # Evidencia fotográfica opcional (URL pública en R2).
    foto_url = Column(String(500))
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


# ----------------------------- Planta de tratamiento -------------------------
class ParametroPlanta(Base):
    __tablename__ = "parametros_planta"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False)
    tipo_agua = Column(Enum(TipoAgua, name="tipo_agua", native_enum=True), nullable=False)
    unidad = Column(String(20))
    valor_min = Column(Numeric(12, 4))
    valor_max = Column(Numeric(12, 4))
    estado = Column(_estado(), nullable=False, server_default=EstadoRegistro.activo.value)
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Medicion(Base):
    __tablename__ = "mediciones"

    id = Column(Integer, primary_key=True)
    parametro_id = Column(Integer, ForeignKey("parametros_planta.id", ondelete="RESTRICT"), nullable=False)
    valor = Column(Numeric(12, 4), nullable=False)
    fecha = Column(Date, nullable=False)
    hora = Column(Time)
    responsable_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    fuera_rango = Column(Boolean, nullable=False, server_default="0")
    accion_correctiva = Column(Text())
    observaciones = Column(Text())
    # Evidencia fotográfica opcional (URL pública en R2).
    foto_url = Column(String(500))
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Dosificacion(Base):
    __tablename__ = "dosificaciones"

    id = Column(Integer, primary_key=True)
    elemento_id = Column(Integer, ForeignKey("elementos_inventario.id", ondelete="RESTRICT"), nullable=False)
    # Cantidad de químico INCORPORADA (ej. 1 L de cloro): esto descuenta inventario.
    cantidad = Column(Numeric(12, 2), nullable=False)
    unidad = Column(String(20))
    # Tasa/caudal con la que está dosificando la bomba (ej. ml/min): solo
    # informativa, NO descuenta inventario. Sirve para estimar cuánto tiempo
    # queda de químico con el stock puesto en el tanque.
    tasa = Column(Numeric(12, 4))
    unidad_tasa = Column(String(20), server_default="ml/min")
    fecha = Column(Date, nullable=False)
    hora = Column(Time)
    responsable_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    observaciones = Column(Text())
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class ActividadPlanta(Base):
    __tablename__ = "actividades_planta"

    id = Column(Integer, primary_key=True)
    tipo = Column(String(80), nullable=False)
    fecha = Column(Date, nullable=False)
    hora = Column(Time)
    responsable_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    estado = Column(_estado(), nullable=False, server_default=EstadoRegistro.activo.value)
    observaciones = Column(Text())
    evidencia = Column(String(255))
    # Evidencia fotográfica opcional (URL pública en R2; `evidencia` sigue
    # siendo la referencia textual libre: código de foto, folio, etc.).
    foto_url = Column(String(500))
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class HoraServicio(Base):
    __tablename__ = "horas_servicio"

    id = Column(Integer, primary_key=True)
    fecha = Column(Date, nullable=False)
    horas = Column(Numeric(6, 2), nullable=False)
    responsable_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    observaciones = Column(Text())
    created_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    updated_by = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"))
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
