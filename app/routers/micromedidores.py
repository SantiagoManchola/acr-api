"""Router de micromedidores (RF-21..RF-36)."""
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..schemas import (
    LecturaCreate,
    LecturaOut,
    MicromedidorCreate,
    MicromedidorOut,
    MicromedidorUpdate,
    SuscriptorCreate,
    SuscriptorOut,
    SuscriptorUpdate,
)
from ..security import get_current_user, get_db, require_role
from ..services.common import sellar, validar_foto_url
from ..services import micromedidores as svc_mm

router = APIRouter(prefix="", tags=["Micromedidores"])

_LECTORES = ["admin", "administrativo", "operario", "fontanero"]
_ESCRITORES = ["admin", "administrativo"]
# Toma de lecturas: el fontanero solo puede hacer esto (sin CRUD de
# suscriptores ni medidores, que siguen en _ESCRITORES).
_TOMADORES_LECTURA = ["admin", "administrativo", "fontanero"]


# ----------------------------- Suscriptores ----------------------------------
@router.get("/suscriptores", response_model=list[SuscriptorOut], summary="Listar suscriptores")
def listar_suscriptores(
    nombre: str | None = None,
    identificacion: str | None = None,
    sector: str | None = None,
    tipo_usuario: str | None = None,
    con_medidor: bool | None = None,
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    return svc_mm.filtrar_suscriptores(
        db, nombre=nombre, identificacion=identificacion, sector=sector,
        tipo_usuario=tipo_usuario, con_medidor=con_medidor,
    )


@router.get("/suscriptores/sectores", response_model=list[str], summary="Sectores disponibles")
def sectores(
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    return svc_mm.sectores_disponibles(db)


@router.post(
    "/suscriptores",
    response_model=SuscriptorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear suscriptor",
)
def crear_suscriptor(
    payload: SuscriptorCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    datos = payload.model_dump()
    # El sector debe existir en el catálogo (se guarda el nombre canónico).
    datos["sector"] = svc_mm.normalizar_sector(db, datos.get("sector"))
    sus = models.Suscriptor(**datos)
    sellar(sus, usuario, nuevo=True)
    db.add(sus)
    db.commit()
    db.refresh(sus)
    return sus


@router.get("/suscriptores/{sid}", response_model=SuscriptorOut, summary="Obtener suscriptor")
def obtener_suscriptor(
    sid: int, db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    sus = db.get(models.Suscriptor, sid)
    if not sus:
        raise HTTPException(404, "Suscriptor no encontrado")
    return sus


@router.get("/suscriptores/{sid}/historial", summary="Historial de un suscriptor (micromedidores y lecturas)")
def historial_suscriptor(
    sid: int, db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    return svc_mm.historial_suscriptor(db, sid)


@router.patch("/suscriptores/{sid}", response_model=SuscriptorOut, summary="Actualizar suscriptor")
def actualizar_suscriptor(
    sid: int,
    payload: SuscriptorUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    sus = db.get(models.Suscriptor, sid)
    if not sus:
        raise HTTPException(404, "Suscriptor no encontrado")
    datos = payload.model_dump(exclude_unset=True)
    if "sector" in datos:
        datos["sector"] = svc_mm.normalizar_sector(db, datos["sector"])
    for k, v in datos.items():
        setattr(sus, k, v)
    sellar(sus, usuario, nuevo=False)
    db.commit()
    db.refresh(sus)
    return sus


@router.delete("/suscriptores/{sid}", summary="Eliminar suscriptor (soft delete)")
def eliminar_suscriptor(
    sid: int, db: Session = Depends(get_db), usuario: models.Usuario = Depends(require_role(_ESCRITORES))
):
    sus = db.get(models.Suscriptor, sid)
    if not sus:
        raise HTTPException(404, "Suscriptor no encontrado")
    sus.estado = models.EstadoRegistro.inactivo
    sellar(sus, usuario, nuevo=False)
    db.commit()
    return {"ok": True}


# ----------------------------- Micromedidores --------------------------------
@router.get("/micromedidores", response_model=list[MicromedidorOut], summary="Listar micromedidores")
def listar_micromedidores(
    serial: str | None = None,
    suscriptor_id: int | None = None,
    estado: str | None = None,
    condicion: str | None = None,
    sector: str | None = None,
    db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    return svc_mm.filtrar_micromedidores(
        db, serial=serial, suscriptor_id=suscriptor_id, estado=estado, sector=sector, condicion=condicion
    )


@router.post(
    "/micromedidores",
    response_model=MicromedidorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear micromedidor",
)
def crear_micromedidor(
    payload: MicromedidorCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    if payload.suscriptor_id and not db.get(models.Suscriptor, payload.suscriptor_id):
        raise HTTPException(400, "suscriptor_id inválido")
    mm = models.Micromedidor(**payload.model_dump())
    sellar(mm, usuario, nuevo=True)
    db.add(mm)
    db.commit()
    db.refresh(mm)
    return mm


@router.get("/micromedidores/{mid}", response_model=MicromedidorOut, summary="Obtener micromedidor")
def obtener_micromedidor(
    mid: int, db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    mm = db.get(models.Micromedidor, mid)
    if not mm:
        raise HTTPException(404, "Micromedidor no encontrado")
    return mm


@router.get("/micromedidores/{mid}/historial", summary="Historial de un micromedidor (lecturas)")
def historial_micromedidor(
    mid: int, db: Session = Depends(get_db), _: models.Usuario = Depends(require_role(_LECTORES))
):
    return svc_mm.historial_micromedidor(db, mid)


@router.patch("/micromedidores/{mid}", response_model=MicromedidorOut, summary="Actualizar micromedidor")
def actualizar_micromedidor(
    mid: int,
    payload: MicromedidorUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    mm = db.get(models.Micromedidor, mid)
    if not mm:
        raise HTTPException(404, "Micromedidor no encontrado")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(mm, k, v)
    sellar(mm, usuario, nuevo=False)
    db.commit()
    db.refresh(mm)
    return mm


@router.delete("/micromedidores/{mid}", summary="Eliminar micromedidor (soft delete)")
def eliminar_micromedidor(
    mid: int, db: Session = Depends(get_db), usuario: models.Usuario = Depends(require_role(_ESCRITORES))
):
    mm = db.get(models.Micromedidor, mid)
    if not mm:
        raise HTTPException(404, "Micromedidor no encontrado")
    mm.estado = models.EstadoRegistro.inactivo
    sellar(mm, usuario, nuevo=False)
    db.commit()
    return {"ok": True}


# ----------------------------- Lecturas --------------------------------------
@router.post(
    "/lecturas",
    response_model=LecturaOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar lectura (calcula consumo/promedio)",
)
def crear_lectura(
    payload: LecturaCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_TOMADORES_LECTURA)),
):
    micromedidor = db.get(models.Micromedidor, payload.micromedidor_id)
    if not micromedidor:
        raise HTTPException(400, "micromedidor_id inválido")
    if micromedidor.estado == models.EstadoRegistro.inactivo:
        raise HTTPException(400, "El micromedidor está inactivo: no se pueden registrar lecturas")
    if not db.get(models.Suscriptor, payload.suscriptor_id):
        raise HTTPException(400, "suscriptor_id inválido")

    estimada = bool(payload.irregular)
    if estimada and payload.lectura is not None:
        raise HTTPException(
            400,
            "Una lectura estimada no lleva valor de medidor: el sistema lo calcula con la lectura previa + promedio histórico",
        )
    if not estimada and payload.lectura is None:
        raise HTTPException(400, "El valor del medidor es obligatorio para una lectura física")

    valor, consumo, promedio_usado = svc_mm.resolver_lectura(
        db, payload.micromedidor_id, payload.lectura, payload.fecha or date.today(), estimada
    )
    lectura = models.Lectura(
        micromedidor_id=payload.micromedidor_id,
        suscriptor_id=payload.suscriptor_id,
        fecha=payload.fecha or date.today(),
        hora=payload.hora or datetime.now().time(),
        lectura=valor,
        consumo=consumo,
        promedio_usado=promedio_usado,
        responsable_id=usuario.id,
        novedad=payload.novedad,
        irregular=payload.irregular,
        foto_url=validar_foto_url(payload.foto_url),
    )
    sellar(lectura, usuario, nuevo=True)
    db.add(lectura)
    db.flush()
    # Frenado automático: 3 lecturas iguales seguidas -> frenado;
    # medición distinta a la anterior estando frenado -> vuelve a bueno.
    svc_mm.evaluar_condicion(db, payload.micromedidor_id)
    db.commit()
    db.refresh(lectura)
    return lectura


@router.get("/lecturas", response_model=list[LecturaOut], summary="Consultar lecturas")
def listar_lecturas(
    micromedidor_id: int | None = None,
    suscriptor_id: int | None = None,
    sector: str | None = None,
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(_LECTORES)),
):
    return svc_mm.filtrar_lecturas(
        db, micromedidor_id=micromedidor_id, suscriptor_id=suscriptor_id,
        sector=sector, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
    )


@router.get(
    "/consumo/sector/{sector}",
    response_model=list[LecturaOut],
    summary="Lecturas/consumo por sector (RF-34)",
)
def consumo_por_sector(
    sector: str,
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(_LECTORES)),
):
    stmt = (
        select(models.Lectura)
        .join(models.Suscriptor, models.Lectura.suscriptor_id == models.Suscriptor.id)
        .where(models.Suscriptor.sector == sector)
        .order_by(models.Lectura.fecha.desc())
    )
    return db.execute(stmt).scalars().all()
