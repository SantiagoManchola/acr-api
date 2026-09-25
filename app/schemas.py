"""Esquemas Pydantic v2 para validación de entrada/salida (RNF-06)."""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .config import settings
from .models import (
    CategoriaTipo,
    CondicionMedidor,
    EstadoRegistro,
    TipoAgua,
    TipoMovimiento,
    TipoUsuario,
)

# Base común con from_attributes (ORM mode)
class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----------------------------- Paginación ------------------------------------
ItemT = TypeVar("ItemT")


class Paginacion(BaseModel, Generic[ItemT]):
    """Sobre estándar de listado paginado server-side."""

    items: List[ItemT]
    total: int
    page: int
    page_size: int
    pages: int


# ----------------------------- Tokens / auth ---------------------------------
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class CambioPassword(BaseModel):
    password_actual: str
    password_nuevo: str = Field(min_length=6)


# ----------------------------- Roles / permisos ------------------------------
class RolOut(_ORM):
    id: int
    nombre: str
    descripcion: Optional[str] = None


class RolCreate(BaseModel):
    nombre: str = Field(min_length=2, max_length=50)
    descripcion: Optional[str] = None


# ----------------------------- Usuarios --------------------------------------
class UsuarioCreate(BaseModel):
    nombre: str = Field(min_length=2, max_length=150)
    identificacion: Optional[str] = None
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    rol_id: int


class UsuarioUpdate(BaseModel):
    nombre: Optional[str] = None
    identificacion: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=6)
    rol_id: Optional[int] = None
    estado: Optional[EstadoRegistro] = None


class UsuarioOut(_ORM):
    id: int
    nombre: str
    identificacion: Optional[str] = None
    username: str
    rol_id: int
    rol: Optional[RolOut] = None
    estado: EstadoRegistro
    ultimo_acceso: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def es_superadmin(self) -> bool:
        return self.username == settings.admin_username


# ----------------------------- Inventario ------------------------------------
class CategoriaCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    tipo: CategoriaTipo
    descripcion: Optional[str] = None


class CategoriaOut(_ORM):
    id: int
    nombre: str
    tipo: CategoriaTipo
    descripcion: Optional[str] = None


class ElementoCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    categoria_id: int
    unidad: Optional[str] = None
    proveedor: Optional[str] = None
    valor: Optional[Decimal] = None
    minimo: Optional[Decimal] = None
    observaciones: Optional[str] = None
    # Stock inicial opcional: sirve para crear el producto ya con existencias
    # en una ubicación concreta (en lugar de registrar una entrada aparte).
    ubicacion_id: Optional[int] = None
    cantidad_inicial: Optional[Decimal] = Field(default=None, ge=0)


class ElementoUpdate(BaseModel):
    nombre: Optional[str] = None
    categoria_id: Optional[int] = None
    unidad: Optional[str] = None
    proveedor: Optional[str] = None
    valor: Optional[Decimal] = None
    minimo: Optional[Decimal] = None
    estado: Optional[EstadoRegistro] = None
    observaciones: Optional[str] = None


class StockUbicacionOut(_ORM):
    id: int
    elemento_id: int
    ubicacion_id: int
    ubicacion: Optional[str] = None
    cantidad: Decimal


class ElementoOut(_ORM):
    id: int
    nombre: str
    categoria_id: int
    cantidad: Decimal
    unidad: Optional[str] = None
    proveedor: Optional[str] = None
    valor: Optional[Decimal] = None
    minimo: Optional[Decimal] = None
    estado: EstadoRegistro
    observaciones: Optional[str] = None
    stock: List[StockUbicacionOut] = []


class UbicacionCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    descripcion: Optional[str] = None


class UbicacionUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, min_length=1, max_length=100)
    descripcion: Optional[str] = None


class UbicacionOut(_ORM):
    id: int
    nombre: str
    descripcion: Optional[str] = None


class TrasladoCreate(BaseModel):
    elemento_id: int
    ubicacion_origen_id: int
    ubicacion_destino_id: int
    cantidad: Decimal = Field(gt=0)
    observaciones: Optional[str] = None
    fecha: Optional[date] = None
    hora: Optional[time] = None
    responsable_id: Optional[int] = None


class TrasladoOut(_ORM):
    id: int
    elemento_id: int
    elemento_nombre: Optional[str] = None
    ubicacion_origen_id: int
    ubicacion_destino_id: int
    ubicacion_origen: Optional[str] = None
    ubicacion_destino: Optional[str] = None
    cantidad: Decimal
    responsable_id: Optional[int] = None
    responsable_nombre: Optional[str] = None
    observaciones: Optional[str] = None
    fecha: date
    hora: Optional[time] = None


class MovimientoCreate(BaseModel):
    ubicacion_id: int
    cantidad: Decimal = Field(gt=0)
    motivo: Optional[str] = None
    observaciones: Optional[str] = None
    fecha: Optional[date] = None
    hora: Optional[time] = None


class MovimientoOut(_ORM):
    id: int
    elemento_id: int
    elemento_nombre: Optional[str] = None
    ubicacion_id: int
    ubicacion: Optional[str] = None
    tipo: TipoMovimiento
    cantidad: Decimal
    responsable_id: Optional[int] = None
    responsable_nombre: Optional[str] = None
    motivo: Optional[str] = None
    observaciones: Optional[str] = None
    fecha: date
    hora: Optional[time] = None


class AlertaOut(BaseModel):
    """Existencias por ubicación por debajo de su mínimo configurado."""

    tipo: str
    id: int
    nombre: str
    categoria: str
    ubicacion: Optional[str] = None
    cantidad: Decimal
    minimo: Optional[Decimal] = None
    unidad: Optional[str] = None


# ----------------------------- Micromedidores --------------------------------
class SuscriptorCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    identificacion: Optional[str] = None
    codigo_usuario: Optional[str] = None
    codigo_facturacion: Optional[str] = None
    tipo_usuario: TipoUsuario = TipoUsuario.residencial
    sector: Optional[str] = None
    direccion: Optional[str] = None


class SuscriptorUpdate(BaseModel):
    nombre: Optional[str] = None
    identificacion: Optional[str] = None
    codigo_usuario: Optional[str] = None
    codigo_facturacion: Optional[str] = None
    tipo_usuario: Optional[TipoUsuario] = None
    sector: Optional[str] = None
    direccion: Optional[str] = None
    estado: Optional[EstadoRegistro] = None


class SuscriptorOut(_ORM):
    id: int
    nombre: str
    identificacion: Optional[str] = None
    codigo_usuario: Optional[str] = None
    codigo_facturacion: Optional[str] = None
    tipo_usuario: TipoUsuario
    sector: Optional[str] = None
    direccion: Optional[str] = None
    estado: EstadoRegistro


class SectorCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)


class SectorUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, min_length=1, max_length=80)
    estado: Optional[EstadoRegistro] = None


class SectorOut(_ORM):
    id: int
    nombre: str
    estado: EstadoRegistro


class MicromedidorCreate(BaseModel):
    serial: str = Field(min_length=1, max_length=50)
    tipo: Optional[str] = None
    suscriptor_id: Optional[int] = None
    direccion: Optional[str] = None
    fecha_instalacion: Optional[date] = None
    # 'bueno' por defecto; 'defectuoso' se marca manualmente. 'frenado' es automático.
    condicion: CondicionMedidor = CondicionMedidor.bueno


class MicromedidorUpdate(BaseModel):
    serial: Optional[str] = None
    tipo: Optional[str] = None
    suscriptor_id: Optional[int] = None
    direccion: Optional[str] = None
    fecha_instalacion: Optional[date] = None
    condicion: Optional[CondicionMedidor] = None
    estado: Optional[EstadoRegistro] = None


class MicromedidorOut(_ORM):
    id: int
    serial: str
    tipo: Optional[str] = None
    suscriptor_id: Optional[int] = None
    suscriptor_nombre: Optional[str] = None
    direccion: Optional[str] = None
    fecha_instalacion: Optional[date] = None
    condicion: CondicionMedidor
    estado: EstadoRegistro


class LecturaCreate(BaseModel):
    micromedidor_id: int
    suscriptor_id: int
    fecha: Optional[date] = None
    hora: Optional[time] = None
    # None cuando la lectura es ESTIMADA (no fue posible tomar la medición):
    # el sistema calcula el valor del medidor con la lectura previa + promedio.
    lectura: Optional[Decimal] = None
    responsable_id: Optional[int] = None
    novedad: Optional[str] = None
    irregular: bool = False
    # URL pública de la foto en R2 (opcional; se sube directo a R2 con /evidencias/presign).
    foto_url: Optional[str] = Field(default=None, max_length=500)


class LecturaOut(_ORM):
    id: int
    micromedidor_id: int
    suscriptor_id: int
    suscriptor_nombre: Optional[str] = None
    medidor_serial: Optional[str] = None
    fecha: date
    hora: Optional[time] = None
    lectura: Decimal
    consumo: Optional[Decimal] = None
    promedio_usado: bool
    responsable_id: Optional[int] = None
    novedad: Optional[str] = None
    irregular: bool
    foto_url: Optional[str] = None


class LecturaFotoUpdate(BaseModel):
    """Adjuntar/reemplazar/quitar la evidencia de una lectura YA registrada.

    Solo foto: los datos de medición (valor, fecha, consumo) no se tocan.
    foto_url=None quita la evidencia. La URL se valida contra el bucket
    público de R2 (misma regla que al crear la lectura).
    """

    foto_url: Optional[str] = Field(default=None, max_length=500)


class SuscriptoresPagina(Paginacion[SuscriptorOut]):
    pass


class MicromedidoresPagina(Paginacion[MicromedidorOut]):
    pass


class LecturasPagina(Paginacion[LecturaOut]):
    pass


# ----------------------------- Planta ----------------------------------------
class ParametroCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    tipo_agua: TipoAgua
    unidad: Optional[str] = None
    valor_min: Optional[Decimal] = None
    valor_max: Optional[Decimal] = None


class ParametroUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo_agua: Optional[TipoAgua] = None
    unidad: Optional[str] = None
    valor_min: Optional[Decimal] = None
    valor_max: Optional[Decimal] = None
    estado: Optional[EstadoRegistro] = None


class ParametroOut(_ORM):
    id: int
    nombre: str
    tipo_agua: TipoAgua
    unidad: Optional[str] = None
    valor_min: Optional[Decimal] = None
    valor_max: Optional[Decimal] = None
    estado: EstadoRegistro


class MedicionCreate(BaseModel):
    parametro_id: int
    valor: Decimal
    fecha: Optional[date] = None
    hora: Optional[time] = None
    responsable_id: Optional[int] = None
    accion_correctiva: Optional[str] = None
    observaciones: Optional[str] = None
    # URL pública de la foto en R2 (opcional; se sube directo a R2 con /evidencias/presign).
    foto_url: Optional[str] = Field(default=None, max_length=500)


class MedicionOut(_ORM):
    id: int
    parametro_id: int
    parametro_nombre: Optional[str] = None
    valor: Decimal
    fecha: date
    hora: Optional[time] = None
    responsable_id: Optional[int] = None
    responsable_nombre: Optional[str] = None
    fuera_rango: bool
    accion_correctiva: Optional[str] = None
    observaciones: Optional[str] = None
    foto_url: Optional[str] = None


class DosificacionCreate(BaseModel):
    elemento_id: int
    cantidad: Decimal = Field(gt=0)  # químico incorporado (descuenta inventario)
    ubicacion_id: Optional[int] = None  # de dónde sale (p. ej. Planta de tratamiento)
    tasa: Optional[Decimal] = None  # tasa/caudal de la bomba (informativa, ej. ml/min)
    unidad_tasa: Optional[str] = None
    fecha: Optional[date] = None
    hora: Optional[time] = None
    responsable_id: Optional[int] = None
    observaciones: Optional[str] = None


class DosificacionOut(_ORM):
    id: int
    elemento_id: int
    elemento_nombre: Optional[str] = None
    cantidad: Decimal
    unidad: Optional[str] = None
    tasa: Optional[Decimal] = None
    unidad_tasa: Optional[str] = None
    fecha: date
    hora: Optional[time] = None
    responsable_id: Optional[int] = None
    responsable_nombre: Optional[str] = None
    observaciones: Optional[str] = None


class ParametroFueraRangoOut(BaseModel):
    """Parámetro cuya ÚLTIMA medición está fuera de rango (estado actual)."""

    parametro_id: int
    parametro: str
    tipo_agua: str
    unidad: Optional[str] = None
    valor_min: Optional[Decimal] = None
    valor_max: Optional[Decimal] = None
    valor: Decimal  # última medición registrada
    fecha: date
    hora: Optional[time] = None
    medicion_id: int
    accion_correctiva: Optional[str] = None


class ActividadCreate(BaseModel):
    tipo: str = Field(min_length=1, max_length=80)
    fecha: Optional[date] = None
    hora: Optional[time] = None
    responsable_id: Optional[int] = None
    observaciones: Optional[str] = None
    evidencia: Optional[str] = None
    # URL pública de la foto en R2 (opcional; se sube directo a R2 con /evidencias/presign).
    foto_url: Optional[str] = Field(default=None, max_length=500)


class ActividadUpdate(BaseModel):
    tipo: Optional[str] = None
    fecha: Optional[date] = None
    hora: Optional[time] = None
    responsable_id: Optional[int] = None
    estado: Optional[EstadoRegistro] = None
    observaciones: Optional[str] = None
    evidencia: Optional[str] = None
    foto_url: Optional[str] = Field(default=None, max_length=500)


class ActividadOut(_ORM):
    id: int
    tipo: str
    fecha: date
    hora: Optional[time] = None
    responsable_id: Optional[int] = None
    responsable_nombre: Optional[str] = None
    estado: EstadoRegistro
    observaciones: Optional[str] = None
    evidencia: Optional[str] = None
    foto_url: Optional[str] = None


class HoraServicioCreate(BaseModel):
    fecha: Optional[date] = None
    horas: Decimal = Field(gt=0)
    responsable_id: Optional[int] = None
    observaciones: Optional[str] = None


class HoraServicioOut(_ORM):
    id: int
    fecha: date
    horas: Decimal
    responsable_id: Optional[int] = None
    responsable_nombre: Optional[str] = None
    observaciones: Optional[str] = None


# ----------------------------- Páginas de listado -----------------------------
class MedicionesPagina(Paginacion[MedicionOut]):
    pass


class ActividadesPagina(Paginacion[ActividadOut]):
    pass


class DosificacionesPagina(Paginacion[DosificacionOut]):
    pass


class HorasPagina(Paginacion[HoraServicioOut]):
    pass


class ElementosPagina(Paginacion[ElementoOut]):
    pass


class MovimientosPagina(Paginacion[MovimientoOut]):
    pass


class TrasladosPagina(Paginacion[TrasladoOut]):
    pass


class UsuariosPagina(Paginacion[UsuarioOut]):
    pass


# ----------------------------- Opciones para selects --------------------------
class SuscriptorOpcionOut(BaseModel):
    id: int
    nombre: str
    estado: EstadoRegistro


class MedidorOpcionOut(BaseModel):
    id: int
    serial: str
    suscriptor_id: Optional[int] = None
    suscriptor_nombre: Optional[str] = None
    direccion: Optional[str] = None
    estado: EstadoRegistro


class ElementoOpcionOut(BaseModel):
    id: int
    nombre: str
    categoria_id: int
    unidad: Optional[str] = None
    estado: EstadoRegistro
    stock: List[StockUbicacionOut] = []


class UsuarioOpcionOut(BaseModel):
    id: int
    nombre: str
    estado: EstadoRegistro


# ----------------------------- Dashboard --------------------------------------
class DashboardQuimico(BaseModel):
    id: int
    nombre: str
    unidad: Optional[str] = None
    cantidad: Decimal
    minimo: Optional[Decimal] = None


class DashboardQuimicosPlanta(BaseModel):
    total: int
    bajos: int
    items: List[DashboardQuimico]


class DashboardFrenado(BaseModel):
    id: int
    serial: str
    suscriptor: Optional[str] = None
    sector: Optional[str] = None


class DashboardConsumoMedidor(BaseModel):
    micromedidor_id: int
    serial: str
    suscriptor: Optional[str] = None
    sector: Optional[str] = None
    total: Decimal
    promedio: Optional[Decimal] = None
    lecturas: int


class DashboardConsumo(BaseModel):
    dias: int
    desde: date
    hasta: date
    total: Decimal
    promedio: Optional[Decimal] = None
    lecturas: int
    por_medidor: List[DashboardConsumoMedidor]


class DashboardMicromedidores(BaseModel):
    suscriptores: int
    medidores: int
    frenados_total: int
    frenados: List[DashboardFrenado]
    consumo: Optional[DashboardConsumo] = None


class DashboardInventario(BaseModel):
    elementos: int
    alertas: int
    solo_oficina: bool


class DashboardPlanta(BaseModel):
    fuera_rango: List[ParametroFueraRangoOut]


class DashboardResumen(BaseModel):
    rol: str
    inventario: Optional[DashboardInventario] = None
    quimicos_planta: Optional[DashboardQuimicosPlanta] = None
    micromedidores: Optional[DashboardMicromedidores] = None
    planta: Optional[DashboardPlanta] = None
