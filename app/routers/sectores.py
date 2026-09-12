"""Router del catálogo de sectores (clasificación de suscriptores).

Lectura para todos los lectores de micromedidores; creación/edición/
inactivación SOLO admin (super admin), para mantener abierta la lista.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..schemas import SectorCreate, SectorOut, SectorUpdate
from ..security import get_current_user, get_db, require_role
from ..services.common import sellar

router = APIRouter(prefix="/sectores", tags=["Sectores"])

_LECTORES = ["admin", "administrativo", "operario", "fontanero"]
_SOLO_ADMIN = ["admin"]


@router.get("", response_model=list[SectorOut], summary="Listar sectores del catálogo")
def listar_sectores(
    solo_activos: bool = False,
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    stmt = select(models.Sector).order_by(models.Sector.nombre)
    if solo_activos:
        stmt = stmt.where(models.Sector.estado == models.EstadoRegistro.activo)
    return db.execute(stmt).scalars().all()


@router.get("/conteo", summary="Suscriptores por sector (para el admin)")
def conteo_por_sector(
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_SOLO_ADMIN))
):
    filas = db.execute(
        select(models.Suscriptor.sector, func.count(models.Suscriptor.id))
        .where(models.Suscriptor.sector.isnot(None))
        .group_by(models.Suscriptor.sector)
    ).all()
    return {sector: n for sector, n in filas}


@router.post(
    "",
    response_model=SectorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear sector (solo admin)",
)
def crear_sector(
    payload: SectorCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_SOLO_ADMIN)),
):
    sec = models.Sector(nombre=payload.nombre.strip())
    sellar(sec, usuario, nuevo=True)
    db.add(sec)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, f"El sector «{sec.nombre}» ya existe")
    db.refresh(sec)
    return sec


@router.patch("/{sid}", response_model=SectorOut, summary="Renombrar o cambiar estado (solo admin)")
def actualizar_sector(
    sid: int,
    payload: SectorUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_SOLO_ADMIN)),
):
    sec = db.get(models.Sector, sid)
    if not sec:
        raise HTTPException(404, "Sector no encontrado")
    datos = payload.model_dump(exclude_unset=True)
    if "nombre" in datos and datos["nombre"] is not None:
        nuevo = datos["nombre"].strip()
        if nuevo != sec.nombre:
            # El renombre se propaga a los suscriptores (es corrección del
            # catálogo, no historia): filtros e historial siguen coherentes.
            anterior = sec.nombre
            sec.nombre = nuevo
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                raise HTTPException(400, f"El sector «{nuevo}» ya existe")
            db.execute(
                models.Suscriptor.__table__.update()
                .where(models.Suscriptor.sector == anterior)
                .values(sector=nuevo)
            )
        del datos["nombre"]
    for k, v in datos.items():
        setattr(sec, k, v)
    sellar(sec, usuario, nuevo=False)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "El sector ya existe")
    db.refresh(sec)
    return sec


@router.delete("/{sid}", summary="Inactivar sector (solo admin)")
def eliminar_sector(
    sid: int, db: Session = Depends(get_db), usuario: models.Usuario = Depends(require_role(_SOLO_ADMIN))
):
    """Soft delete: el sector deja de ser asignable pero el historial se conserva."""
    sec = db.get(models.Sector, sid)
    if not sec:
        raise HTTPException(404, "Sector no encontrado")
    sec.estado = models.EstadoRegistro.inactivo
    sellar(sec, usuario, nuevo=False)
    db.commit()
    return {"ok": True}
