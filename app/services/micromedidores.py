"""Lógica de micromedidores: cálculo de consumo y promedio histórico.

Regla de negocio 1 y 2: lectura mensual; cuando NO es posible tomar la
medición física se registra una lectura ESTIMADA: el valor del medidor se
calcula como lectura previa + promedio histórico de consumo
(promedio_usado=True) y el consumo registrado es ese promedio.
"""
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from .common import condiciones_busqueda


def _lectura_previa(db: Session, micromedidor_id: int, fecha):
    """Última lectura registrada hasta `fecha` (incluida la misma fecha: lecturas
    registradas antes el mismo día sí cuentan como previas)."""
    return (
        db.execute(
            select(models.Lectura)
            .where(
                models.Lectura.micromedidor_id == micromedidor_id,
                models.Lectura.fecha <= fecha,
            )
            .order_by(models.Lectura.fecha.desc(), models.Lectura.id.desc())
        )
        .scalars()
        .first()
    )


def _promedio_historico(db: Session, micromedidor_id: int):
    """Promedio de los consumos de las ÚLTIMAS 6 lecturas (None si no hay ninguno).

    Solo se usan las 6 mediciones más recientes, no todo el histórico, para que
    la estimación refleje el consumo actual del suscriptor.
    """
    ultimas = (
        select(models.Lectura.consumo)
        .where(
            models.Lectura.micromedidor_id == micromedidor_id,
            models.Lectura.consumo.isnot(None),
        )
        .order_by(models.Lectura.fecha.desc(), models.Lectura.id.desc())
        .limit(6)
        .subquery()
    )
    return db.execute(select(func.avg(ultimas.c.consumo))).scalar()


def resolver_lectura(
    db: Session,
    micromedidor_id: int,
    lectura: Decimal | None,
    fecha,
    estimada: bool = False,
) -> tuple[Decimal, Decimal | None, bool]:
    """Resuelve (valor_del_medidor, consumo, promedio_usado) al registrar una lectura.

    - Lectura física: consumo = lectura - lectura previa (None si es la primera).
    - Lectura estimada (no fue posible tomar la medición): valor del medidor =
      lectura previa + promedio histórico; consumo = ese promedio
      (promedio_usado=True).
    """
    previa = _lectura_previa(db, micromedidor_id, fecha)

    if estimada:
        if previa is None or previa.lectura is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No hay lectura previa para estimar el valor del medidor; registre primero una lectura física.",
            )
        promedio = _promedio_historico(db, micromedidor_id)
        if promedio is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No hay consumos históricos para estimar la lectura.",
            )
        valor = Decimal(str(previa.lectura)) + Decimal(str(promedio))
        return valor, Decimal(str(promedio)), True

    if previa is not None and previa.lectura is not None:
        return (
            Decimal(str(lectura)),
            Decimal(str(lectura)) - Decimal(str(previa.lectura)),
            False,
        )
    return Decimal(str(lectura)), None, False


def filtrar_suscriptores(db: Session, *, nombre=None, identificacion=None, sector=None,
                         tipo_usuario=None, con_medidor: bool | None = None):
    stmt = select(models.Suscriptor)
    if nombre:
        stmt = stmt.where(*condiciones_busqueda(models.Suscriptor.nombre, nombre))
    if identificacion:
        stmt = stmt.where(*condiciones_busqueda(models.Suscriptor.identificacion, identificacion))
    if sector:
        stmt = stmt.where(models.Suscriptor.sector.ilike(f"%{sector}%"))
    if tipo_usuario:
        stmt = stmt.where(models.Suscriptor.tipo_usuario == tipo_usuario)
    if con_medidor is not None:
        con = select(models.Micromedidor.suscriptor_id).where(
            models.Micromedidor.suscriptor_id.isnot(None)
        )
        if con_medidor:
            stmt = stmt.where(models.Suscriptor.id.in_(con))
        else:
            stmt = stmt.where(models.Suscriptor.id.notin_(con))
    return db.execute(stmt.order_by(models.Suscriptor.nombre)).scalars().all()


def _valor_lectura(l) -> Decimal:
    """Valor de la lectura normalizado a la escala de la columna (12,3).

    Necesario porque la lectura recién insertada llega como Decimal('100')
    mientras las persistidas vuelven de MySQL como Decimal('100.000'):
    comparar str() daría falsos 'diferentes' y rompería el frenado.
    """
    return Decimal(str(l.lectura)).quantize(Decimal("0.001"))


def evaluar_condicion(db: Session, micromedidor_id: int):
    """Estado operativo del medidor según sus lecturas (bueno/defectuoso/frenado).

    Frenado automático: cuando las 3 últimas lecturas mensuales son idénticas
    el contador está frenado (no registra paso de agua). El aviso permanece
    hasta que llegue una medición distinta a la anterior, momento en el que el
    medidor vuelve a 'bueno'. 'defectuoso' (se marca) lo fija el operario.
    """
    mm = db.get(models.Micromedidor, micromedidor_id)
    if mm is None:
        return None
    ultimas = db.execute(
        select(models.Lectura)
        .where(models.Lectura.micromedidor_id == micromedidor_id)
        .order_by(models.Lectura.fecha.desc(), models.Lectura.id.desc())
        .limit(3)
    ).scalars().all()

    # 3 lecturas consecutivas con el mismo valor -> frenado
    if len(ultimas) >= 3 and len({_valor_lectura(l) for l in ultimas}) == 1:
        mm.condicion = models.CondicionMedidor.frenado
        return mm.condicion

    # Estaba frenado y la medición más reciente ya difiere -> sale de frenado
    if (
        mm.condicion == models.CondicionMedidor.frenado
        and len(ultimas) >= 2
        and _valor_lectura(ultimas[0]) != _valor_lectura(ultimas[1])
    ):
        mm.condicion = models.CondicionMedidor.bueno
        return mm.condicion
    return None


def filtrar_micromedidores(db: Session, *, serial=None, suscriptor_id=None, estado=None, sector=None, condicion=None):
    stmt = select(models.Micromedidor)
    if serial:
        stmt = stmt.where(*condiciones_busqueda(models.Micromedidor.serial, serial))
    if suscriptor_id:
        stmt = stmt.where(models.Micromedidor.suscriptor_id == suscriptor_id)
    if estado:
        stmt = stmt.where(models.Micromedidor.estado == estado)
    if condicion:
        stmt = stmt.where(models.Micromedidor.condicion == condicion)
    if sector:
        stmt = stmt.join(models.Suscriptor, models.Micromedidor.suscriptor_id == models.Suscriptor.id)
        stmt = stmt.where(models.Suscriptor.sector.ilike(f"%{sector}%"))
    return db.execute(stmt.order_by(models.Micromedidor.serial)).scalars().all()


def filtrar_lecturas(db: Session, *, micromedidor_id=None, suscriptor_id=None,
                     sector=None, fecha_inicio=None, fecha_fin=None):
    stmt = select(models.Lectura)
    if micromedidor_id:
        stmt = stmt.where(models.Lectura.micromedidor_id == micromedidor_id)
    if suscriptor_id:
        stmt = stmt.where(models.Lectura.suscriptor_id == suscriptor_id)
    if sector:
        stmt = stmt.join(models.Suscriptor, models.Lectura.suscriptor_id == models.Suscriptor.id)
        stmt = stmt.where(models.Suscriptor.sector.ilike(f"%{sector}%"))
    if fecha_inicio:
        stmt = stmt.where(models.Lectura.fecha >= fecha_inicio)
    if fecha_fin:
        stmt = stmt.where(models.Lectura.fecha <= fecha_fin)
    return db.execute(
        stmt.order_by(models.Lectura.fecha.desc(), models.Lectura.hora.desc())
    ).scalars().all()


def sectores_disponibles(db: Session):
    """Sectores ACTIVOS del catálogo (para filtros y formularios)."""
    return [s.nombre for s in db.execute(
        select(models.Sector)
        .where(models.Sector.estado == models.EstadoRegistro.activo)
        .order_by(models.Sector.nombre)
    ).scalars().all()]


def normalizar_sector(db: Session, sector: str | None) -> str | None:
    """Valida el sector contra el catálogo y devuelve el nombre canónico.

    Vacío/None -> None (sin sector). Insensible a mayúsculas y espacios.
    Solo admite sectores ACTIVOS (los inactivos conservan el historial pero
    ya no son asignables). Lanza 400 si no existe en el catálogo.
    """
    if sector is None or not str(sector).strip():
        return None
    desde = str(sector).strip()
    fila = db.execute(
        select(models.Sector).where(func.lower(models.Sector.nombre) == desde.lower())
    ).scalars().first()
    if fila is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Sector «{desde}» no válido: créelo primero en el catálogo de sectores.",
        )
    if fila.estado != models.EstadoRegistro.activo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El sector «{fila.nombre}» está inactivo y ya no es asignable.",
        )
    return fila.nombre


def historial_suscriptor(db: Session, sid: int):
    suscriptor = db.get(models.Suscriptor, sid)
    micromedidores = db.execute(
        select(models.Micromedidor).where(models.Micromedidor.suscriptor_id == sid)
    ).scalars().all()
    lecturas = db.execute(
        select(models.Lectura).where(models.Lectura.suscriptor_id == sid)
        .order_by(models.Lectura.fecha.desc(), models.Lectura.hora.desc())
    ).scalars().all()
    return {
        "suscriptor": suscriptor,
        "micromedidores": micromedidores,
        "lecturas": lecturas,
    }


def historial_micromedidor(db: Session, mid: int):
    micromedidor = db.get(models.Micromedidor, mid)
    lecturas = db.execute(
        select(models.Lectura).where(models.Lectura.micromedidor_id == mid)
        .order_by(models.Lectura.fecha.desc(), models.Lectura.hora.desc())
    ).scalars().all()
    # Promedio histórico validado: misma regla que usa la estimación
    # (promedio de los consumos de las últimas 6 lecturas con consumo).
    promedio = _promedio_historico(db, mid)
    consumos_validos = db.execute(
        select(func.count(models.Lectura.id)).where(
            models.Lectura.micromedidor_id == mid,
            models.Lectura.consumo.isnot(None),
        )
    ).scalar() or 0
    return {
        "micromedidor": micromedidor,
        "lecturas": lecturas,
        "promedio_historico": float(promedio) if promedio is not None else None,
        "promedio_base_n": min(int(consumos_validos or 0), 6),
        "promedio_total_lecturas": int(consumos_validos or 0),
    }
