"""Router de planta de tratamiento (RF-37..RF-54)."""
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..schemas import (
    ActividadCreate,
    ActividadOut,
    ActividadUpdate,
    ActividadesPagina,
    DosificacionCreate,
    DosificacionOut,
    DosificacionesPagina,
    HoraServicioCreate,
    HoraServicioOut,
    HorasPagina,
    MedicionCreate,
    MedicionOut,
    MedicionesPagina,
    ParametroCreate,
    ParametroFueraRangoOut,
    ParametroOut,
    ParametroUpdate,
    TipoMovimiento,
)
from ..security import get_current_user, get_db, require_role
from ..services.common import sellar, validar_foto_url, como_pagina
from ..services import planta as svc_planta

router = APIRouter(prefix="/planta", tags=["Planta de tratamiento"])

_LECTORES = ["admin", "operario"]
_ESCRITORES = ["admin", "operario"]
# CRUD de parámetros: solo admin (el operario no puede crearlos ni editarlos).
_SOLO_ADMIN = ["admin"]


# ----------------------------- Parámetros ------------------------------------
@router.get("/parametros", response_model=list[ParametroOut], summary="Listar parámetros")
def listar_parametros(
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    return db.execute(select(models.ParametroPlanta)).scalars().all()


@router.post(
    "/parametros",
    response_model=ParametroOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear parámetro",
)
def crear_parametro(
    payload: ParametroCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_SOLO_ADMIN)),
):
    p = models.ParametroPlanta(**payload.model_dump())
    sellar(p, usuario, nuevo=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.patch("/parametros/{pid}", response_model=ParametroOut, summary="Actualizar parámetro")
def actualizar_parametro(
    pid: int,
    payload: ParametroUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_SOLO_ADMIN)),
):
    p = db.get(models.ParametroPlanta, pid)
    if not p:
        raise HTTPException(404, "Parámetro no encontrado")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    sellar(p, usuario, nuevo=False)
    db.commit()
    db.refresh(p)
    return p


# ----------------------------- Mediciones ------------------------------------
@router.post(
    "/mediciones",
    response_model=MedicionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar medición (marca fuera de rango)",
)
def crear_medicion(
    payload: MedicionCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    parametro = db.get(models.ParametroPlanta, payload.parametro_id)
    if not parametro:
        raise HTTPException(400, "parametro_id inválido")
    fuera = svc_planta.fuera_de_rango(parametro, payload.valor)
    med = models.Medicion(
        parametro_id=payload.parametro_id,
        valor=payload.valor,
        fecha=payload.fecha or date.today(),
        hora=payload.hora or datetime.now().time(),
        responsable_id=payload.responsable_id or usuario.id,
        fuera_rango=fuera,
        accion_correctiva=payload.accion_correctiva,
        observaciones=payload.observaciones,
        foto_url=validar_foto_url(payload.foto_url),
    )
    sellar(med, usuario, nuevo=True)
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


@router.get("/mediciones", response_model=MedicionesPagina, summary="Listar mediciones (paginado)")
def listar_mediciones(
    parametro_id: int | None = None,
    fuera_rango: bool | None = None,
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    orden: str | None = Query(default=None, description="Campo de orden (whitelist del servicio)"),
    dir_orden: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    items, total = svc_planta.filtrar_mediciones(
        db, parametro_id=parametro_id, fuera_rango=fuera_rango,
        fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
        page=page, page_size=page_size, orden=orden, dir_orden=dir_orden,
    )
    return como_pagina(items, total, page, page_size)


@router.get(
    "/parametros-fuera-rango",
    response_model=list[ParametroFueraRangoOut],
    summary="Parámetros fuera de rango según su última medición (estado actual)",
)
def parametros_fuera_rango(
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    """No lista mediciones pasadas: solo alerta el parámetro cuya medición más
    reciente está fuera de rango. Al ajustar el proceso y registrar una nueva
    medición en rango, el parámetro deja de alertar."""
    return svc_planta.parametros_fuera_rango(db)


# ----------------------------- Insumos / dosificación ------------------------
@router.post(
    "/dosificaciones",
    response_model=DosificacionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar dosificación (salida de insumo/químico)",
)
def crear_dosificacion(
    payload: DosificacionCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    elemento = db.get(models.ElementoInventario, payload.elemento_id)
    if not elemento:
        raise HTTPException(400, "elemento_id inválido")
    if payload.ubicacion_id is not None and not db.get(models.Ubicacion, payload.ubicacion_id):
        raise HTTPException(400, "ubicacion_id inválida")
    # La dosificación es un punto de salida del inventario: descuenta del stock
    # del insumo/químico (punto 5). Si viene ubicacion_id (p. ej. Planta de
    # tratamiento), SOLO descuenta de esa ubicación.
    fecha = payload.fecha or date.today()
    hora = payload.hora or datetime.now().time()
    descuentos = svc_planta.aplicar_dosificacion(db, elemento, payload.cantidad, payload.ubicacion_id)
    d = models.Dosificacion(
        elemento_id=payload.elemento_id,
        cantidad=payload.cantidad,
        unidad=elemento.unidad,
        tasa=payload.tasa,
        unidad_tasa=payload.unidad_tasa or "ml/min",
        fecha=fecha,
        hora=hora,
        responsable_id=payload.responsable_id or usuario.id,
        observaciones=payload.observaciones,
    )
    sellar(d, usuario, nuevo=True)
    db.add(d)
    # Registra la salida en el historial de movimientos del inventario por cada
    # ubicación descontada, para mantener la trazabilidad.
    for ubicacion_id, cant in descuentos:
        movimiento = models.MovimientoInventario(
            elemento_id=elemento.id,
            ubicacion_id=ubicacion_id,
            tipo=TipoMovimiento.salida,
            cantidad=cant,
            responsable_id=payload.responsable_id or usuario.id,
            motivo="Dosificación",
            observaciones=payload.observaciones,
            fecha=fecha,
            hora=hora,
        )
        sellar(movimiento, usuario, nuevo=True)
        db.add(movimiento)
    db.commit()
    db.refresh(d)
    return d


@router.get("/dosificaciones", response_model=DosificacionesPagina, summary="Listar dosificaciones (paginado)")
def listar_dosificaciones(
    elemento_id: int | None = None,
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    orden: str | None = Query(default=None, description="Campo de orden (whitelist del servicio)"),
    dir_orden: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    items, total = svc_planta.filtrar_dosificaciones(
        db, elemento_id=elemento_id, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
        page=page, page_size=page_size, orden=orden, dir_orden=dir_orden,
    )
    return como_pagina(items, total, page, page_size)


# ----------------------------- Actividades -----------------------------------
@router.post(
    "/actividades",
    response_model=ActividadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar actividad de planta",
)
def crear_actividad(
    payload: ActividadCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    a = models.ActividadPlanta(
        tipo=payload.tipo,
        fecha=payload.fecha or date.today(),
        hora=payload.hora or datetime.now().time(),
        responsable_id=payload.responsable_id or usuario.id,
        observaciones=payload.observaciones,
        evidencia=payload.evidencia,
        foto_url=validar_foto_url(payload.foto_url),
        estado=models.EstadoRegistro.activo,
    )
    sellar(a, usuario, nuevo=True)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.get("/actividades", response_model=ActividadesPagina, summary="Listar actividades de planta (paginado)")
def listar_actividades(
    tipo: str | None = None,
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    orden: str | None = Query(default=None, description="Campo de orden (whitelist del servicio)"),
    dir_orden: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    items, total = svc_planta.filtrar_actividades(
        db, tipo=tipo, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
        page=page, page_size=page_size, orden=orden, dir_orden=dir_orden,
    )
    return como_pagina(items, total, page, page_size)


@router.patch("/actividades/{aid}", response_model=ActividadOut, summary="Actualizar actividad")
def actualizar_actividad(
    aid: int,
    payload: ActividadUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    a = db.get(models.ActividadPlanta, aid)
    if not a:
        raise HTTPException(404, "Actividad no encontrada")
    datos = payload.model_dump(exclude_unset=True)
    if "foto_url" in datos:
        datos["foto_url"] = validar_foto_url(datos["foto_url"])
    for k, v in datos.items():
        setattr(a, k, v)
    sellar(a, usuario, nuevo=False)
    db.commit()
    db.refresh(a)
    return a


# ----------------------------- Horas de servicio -----------------------------
@router.post(
    "/horas-servicio",
    response_model=HoraServicioOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar horas de servicio",
)
def crear_horas(
    payload: HoraServicioCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    h = models.HoraServicio(
        fecha=payload.fecha or date.today(),
        horas=payload.horas,
        responsable_id=payload.responsable_id or usuario.id,
        observaciones=payload.observaciones,
    )
    sellar(h, usuario, nuevo=True)
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@router.get("/horas-servicio", response_model=HorasPagina | list[HoraServicioOut], summary="Listar horas de servicio (paginado opt-in)")
def listar_horas(
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    # Paginación opt-in: el gráfico anual (GraficoHoras) pide el rango completo
    # sin `page`; la tabla del CMS envía page/page_size.
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=20, ge=1, le=366),
    orden: str | None = Query(default=None, description="Campo de orden (whitelist del servicio)"),
    dir_orden: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    items, total = svc_planta.filtrar_horas(
        db, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
        page=page, page_size=page_size, orden=orden, dir_orden=dir_orden,
    )
    if page is None:
        return items
    return como_pagina(items, total, page, page_size)
