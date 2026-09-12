"""Lógica de planta: detección de valores fuera de rango (RF-48/RF-49)."""
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models


def fuera_de_rango(parametro: models.ParametroPlanta, valor: Decimal) -> bool:
    """Compara el valor con valor_min/valor_max configurables (regla 9)."""
    v = Decimal(str(valor))
    if parametro.valor_min is not None and v < Decimal(str(parametro.valor_min)):
        return True
    if parametro.valor_max is not None and v > Decimal(str(parametro.valor_max)):
        return True
    return False


def parametros_fuera_rango(db: Session):
    """Parámetros fuera de rango según su ÚLTIMA medición (estado actual).

    Cada medición guarda su bandera `fuera_rango` como historial, pero la
    alerta ya NO lista mediciones pasadas: solo alerta el parámetro cuya
    medición más reciente está fuera de rango. Si se ajusta el proceso y la
    siguiente medición entra en rango, el parámetro deja de alertar solo.
    """
    parametros = db.execute(
        select(models.ParametroPlanta).where(
            models.ParametroPlanta.estado == models.EstadoRegistro.activo
        )
    ).scalars().all()
    resultado = []
    for p in parametros:
        ultima = db.execute(
            select(models.Medicion)
            .where(models.Medicion.parametro_id == p.id)
            .order_by(
                models.Medicion.fecha.desc(),
                models.Medicion.hora.desc(),
                models.Medicion.id.desc(),
            )
            .limit(1)
        ).scalars().first()
        if ultima is not None and ultima.fuera_rango:
            resultado.append({
                "parametro_id": p.id,
                "parametro": p.nombre,
                "tipo_agua": p.tipo_agua.value if hasattr(p.tipo_agua, "value") else str(p.tipo_agua),
                "unidad": p.unidad,
                "valor_min": p.valor_min,
                "valor_max": p.valor_max,
                "valor": ultima.valor,
                "fecha": ultima.fecha,
                "hora": ultima.hora,
                "medicion_id": ultima.id,
                "accion_correctiva": ultima.accion_correctiva,
            })
    return resultado


def aplicar_dosificacion(
    db: Session,
    elemento: models.ElementoInventario,
    cantidad: Decimal,
    ubicacion_id: int | None = None,
):
    """Descuenta la cantidad de químico INCORPORADA (ej. 1 L) del stock del insumo.

    Si se indica `ubicacion_id` (p. ej. Planta de tratamiento), descuenta SOLO de
    esa ubicación: el stock de otras sedes no se toca. Si no, reparte empezando
    por la ubicación con más stock. Devuelve [(ubicacion_id, cantidad), ...] para
    la trazabilidad. La tasa (ml/min) es informativa y NO se descuenta.
    """
    c = Decimal(str(cantidad))
    if ubicacion_id is not None:
        stock = db.execute(
            select(models.StockUbicacion).where(
                models.StockUbicacion.elemento_id == elemento.id,
                models.StockUbicacion.ubicacion_id == ubicacion_id,
            )
        ).scalar_one_or_none()
        disponible = Decimal(str(stock.cantidad)) if stock else Decimal("0")
        if c > disponible:
            ubs = {u.id: u.nombre for u in db.execute(select(models.Ubicacion)).scalars().all()}
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Stock insuficiente de '{elemento.nombre}' en "
                    f"'{ubs.get(ubicacion_id, ubicacion_id)}': disponible {disponible}, requerido {c}"
                ),
            )
        stock.cantidad = disponible - c
        return [(ubicacion_id, c)]

    stocks = db.execute(
        select(models.StockUbicacion)
        .where(models.StockUbicacion.elemento_id == elemento.id)
        .order_by(models.StockUbicacion.cantidad.desc())
    ).scalars().all()
    disponible = sum((Decimal(str(s.cantidad)) for s in stocks), Decimal("0"))
    if c > disponible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock insuficiente de '{elemento.nombre}': disponible {disponible}, requerido {c}",
        )
    restante = c
    descuentos = []
    for s in stocks:
        if restante <= 0:
            break
        sc = Decimal(str(s.cantidad))
        if sc <= 0:
            continue
        tomar = min(sc, restante)
        s.cantidad = sc - tomar
        descuentos.append((s.ubicacion_id, tomar))
        restante -= tomar
    return descuentos


def _rango_fechas(stmt, modelo, fecha_inicio, fecha_fin):
    if fecha_inicio:
        stmt = stmt.where(modelo.fecha >= fecha_inicio)
    if fecha_fin:
        stmt = stmt.where(modelo.fecha <= fecha_fin)
    return stmt


def filtrar_mediciones(db: Session, *, parametro_id=None, fuera_rango=None,
                        fecha_inicio=None, fecha_fin=None):
    stmt = select(models.Medicion)
    if parametro_id:
        stmt = stmt.where(models.Medicion.parametro_id == parametro_id)
    if fuera_rango is not None:
        stmt = stmt.where(models.Medicion.fuera_rango.is_(fuera_rango))
    stmt = _rango_fechas(stmt, models.Medicion, fecha_inicio, fecha_fin)
    return db.execute(stmt.order_by(models.Medicion.fecha.desc(), models.Medicion.hora.desc())).scalars().all()


def filtrar_actividades(db: Session, *, tipo=None, fecha_inicio=None, fecha_fin=None):
    stmt = select(models.ActividadPlanta)
    if tipo:
        stmt = stmt.where(models.ActividadPlanta.tipo == tipo)
    stmt = _rango_fechas(stmt, models.ActividadPlanta, fecha_inicio, fecha_fin)
    return db.execute(stmt.order_by(models.ActividadPlanta.fecha.desc(), models.ActividadPlanta.hora.desc())).scalars().all()


def filtrar_dosificaciones(db: Session, *, elemento_id=None, fecha_inicio=None, fecha_fin=None):
    stmt = select(models.Dosificacion)
    if elemento_id:
        stmt = stmt.where(models.Dosificacion.elemento_id == elemento_id)
    stmt = _rango_fechas(stmt, models.Dosificacion, fecha_inicio, fecha_fin)
    return db.execute(stmt.order_by(models.Dosificacion.fecha.desc(), models.Dosificacion.hora.desc())).scalars().all()


def filtrar_horas(db: Session, *, fecha_inicio=None, fecha_fin=None):
    stmt = select(models.HoraServicio)
    stmt = _rango_fechas(stmt, models.HoraServicio, fecha_inicio, fecha_fin)
    return db.execute(stmt.order_by(models.HoraServicio.fecha.desc())).scalars().all()
