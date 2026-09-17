"""Lógica de planta: detección de valores fuera de rango (RF-48/RF-49)."""
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .common import aplicar_paginacion, orden_validado


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
                        fecha_inicio=None, fecha_fin=None,
                        page: int | None = None, page_size: int = 20,
                        orden: str | None = None, dir_orden: str | None = None):
    """Lista (o página) de mediciones con parámetro y responsable embebidos."""
    stmt = (
        select(models.Medicion, models.ParametroPlanta.nombre, models.Usuario.nombre)
        .join(models.ParametroPlanta, models.Medicion.parametro_id == models.ParametroPlanta.id)
        .outerjoin(models.Usuario, models.Medicion.responsable_id == models.Usuario.id)
    )
    if parametro_id:
        stmt = stmt.where(models.Medicion.parametro_id == parametro_id)
    if fuera_rango is not None:
        stmt = stmt.where(models.Medicion.fuera_rango.is_(fuera_rango))
    stmt = _rango_fechas(stmt, models.Medicion, fecha_inicio, fecha_fin)

    permitidos = {
        "fecha": models.Medicion.fecha,
        "hora": models.Medicion.hora,
        "parametro": models.ParametroPlanta.nombre,
        "tipo_agua": models.ParametroPlanta.tipo_agua,
        "unidad": models.ParametroPlanta.unidad,
        "valor": models.Medicion.valor,
        "fuera_rango": models.Medicion.fuera_rango,
        "responsable": models.Usuario.nombre,
        "accion_correctiva": models.Medicion.accion_correctiva,
    }
    por_defecto = lambda s: s.order_by(
        models.Medicion.fecha.desc(), models.Medicion.hora.desc(), models.Medicion.id.desc()
    )
    ordenar = orden_validado(
        orden, dir_orden, permitidos, por_defecto, desempate=models.Medicion.id.desc()
    )
    filas, total = aplicar_paginacion(db, stmt, ordenar, page, page_size, many=True)
    items = [
        {**{c.name: getattr(m, c.name) for c in models.Medicion.__table__.columns},
         "parametro_nombre": param,
         "responsable_nombre": resp}
        for (m, param, resp) in filas
    ]
    return items, total


def filtrar_actividades(db: Session, *, tipo=None, fecha_inicio=None, fecha_fin=None,
                        page: int | None = None, page_size: int = 20,
                        orden: str | None = None, dir_orden: str | None = None):
    stmt = (
        select(models.ActividadPlanta, models.Usuario.nombre)
        .outerjoin(models.Usuario, models.ActividadPlanta.responsable_id == models.Usuario.id)
    )
    if tipo:
        stmt = stmt.where(models.ActividadPlanta.tipo == tipo)
    stmt = _rango_fechas(stmt, models.ActividadPlanta, fecha_inicio, fecha_fin)

    permitidos = {
        "fecha": models.ActividadPlanta.fecha,
        "hora": models.ActividadPlanta.hora,
        "tipo": models.ActividadPlanta.tipo,
        "responsable": models.Usuario.nombre,
        "estado": models.ActividadPlanta.estado,
        "observaciones": models.ActividadPlanta.observaciones,
        "evidencia": models.ActividadPlanta.evidencia,
    }
    por_defecto = lambda s: s.order_by(
        models.ActividadPlanta.fecha.desc(), models.ActividadPlanta.hora.desc(), models.ActividadPlanta.id.desc()
    )
    ordenar = orden_validado(
        orden, dir_orden, permitidos, por_defecto, desempate=models.ActividadPlanta.id.desc()
    )
    filas, total = aplicar_paginacion(db, stmt, ordenar, page, page_size, many=True)
    items = [
        {**{c.name: getattr(a, c.name) for c in models.ActividadPlanta.__table__.columns},
         "responsable_nombre": nombre}
        for (a, nombre) in filas
    ]
    return items, total


def filtrar_dosificaciones(db: Session, *, elemento_id=None, fecha_inicio=None, fecha_fin=None,
                           page: int | None = None, page_size: int = 20,
                           orden: str | None = None, dir_orden: str | None = None):
    stmt = (
        select(models.Dosificacion, models.ElementoInventario.nombre, models.Usuario.nombre)
        .join(models.ElementoInventario, models.Dosificacion.elemento_id == models.ElementoInventario.id)
        .outerjoin(models.Usuario, models.Dosificacion.responsable_id == models.Usuario.id)
    )
    if elemento_id:
        stmt = stmt.where(models.Dosificacion.elemento_id == elemento_id)
    stmt = _rango_fechas(stmt, models.Dosificacion, fecha_inicio, fecha_fin)

    permitidos = {
        "fecha": models.Dosificacion.fecha,
        "hora": models.Dosificacion.hora,
        "insumo": models.ElementoInventario.nombre,
        "cantidad": models.Dosificacion.cantidad,
        "unidad": models.Dosificacion.unidad,
        "tasa": models.Dosificacion.tasa,
        "responsable": models.Usuario.nombre,
        "observaciones": models.Dosificacion.observaciones,
    }
    por_defecto = lambda s: s.order_by(
        models.Dosificacion.fecha.desc(), models.Dosificacion.hora.desc(), models.Dosificacion.id.desc()
    )
    ordenar = orden_validado(
        orden, dir_orden, permitidos, por_defecto, desempate=models.Dosificacion.id.desc()
    )
    filas, total = aplicar_paginacion(db, stmt, ordenar, page, page_size, many=True)
    items = [
        {**{c.name: getattr(d, c.name) for c in models.Dosificacion.__table__.columns},
         "elemento_nombre": elem,
         "responsable_nombre": resp}
        for (d, elem, resp) in filas
    ]
    return items, total


def filtrar_horas(db: Session, *, fecha_inicio=None, fecha_fin=None,
                  page: int | None = None, page_size: int = 20,
                  orden: str | None = None, dir_orden: str | None = None):
    stmt = (
        select(models.HoraServicio, models.Usuario.nombre)
        .outerjoin(models.Usuario, models.HoraServicio.responsable_id == models.Usuario.id)
    )
    stmt = _rango_fechas(stmt, models.HoraServicio, fecha_inicio, fecha_fin)

    permitidos = {
        "fecha": models.HoraServicio.fecha,
        "horas": models.HoraServicio.horas,
        "responsable": models.Usuario.nombre,
        "observaciones": models.HoraServicio.observaciones,
    }
    por_defecto = lambda s: s.order_by(models.HoraServicio.fecha.desc(), models.HoraServicio.id.desc())
    ordenar = orden_validado(
        orden, dir_orden, permitidos, por_defecto, desempate=models.HoraServicio.id.desc()
    )
    filas, total = aplicar_paginacion(db, stmt, ordenar, page, page_size, many=True)
    items = [
        {**{c.name: getattr(h, c.name) for c in models.HoraServicio.__table__.columns},
         "responsable_nombre": nombre}
        for (h, nombre) in filas
    ]
    return items, total
