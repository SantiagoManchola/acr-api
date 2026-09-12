"""Lógica de inventario: entradas/salidas y alertas (RF-10..RF-18)."""
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..schemas import TipoMovimiento


def ubicacion_por_nombre(db: Session, *candidatos: str):
    """Ubicación por nombre con coincidencia exacta primero (insensible a caso).

    Evita ambigüedades como 'Oficina' vs 'Nueva oficina': se prueban los
    candidatos exactos en orden y solo al final un parcial determinista (por id).
    """
    for c in candidatos:
        u = db.execute(
            select(models.Ubicacion).where(func.lower(models.Ubicacion.nombre) == c.lower())
        ).scalars().first()
        if u:
            return u
    if candidatos:
        return db.execute(
            select(models.Ubicacion)
            .where(models.Ubicacion.nombre.ilike(f"%{candidatos[0]}%"))
            .order_by(models.Ubicacion.id)
        ).scalars().first()
    return None


def _obtener_stock(db: Session, elemento_id: int, ubicacion_id: int):
    """Devuelve el stock del producto en la ubicación (o None si no existe)."""
    return db.execute(
        select(models.StockUbicacion).where(
            models.StockUbicacion.elemento_id == elemento_id,
            models.StockUbicacion.ubicacion_id == ubicacion_id,
        )
    ).scalar_one_or_none()


def _crear_o_actualizar_stock(db: Session, elemento_id: int, ubicacion_id: int, cantidad: Decimal):
    stock = _obtener_stock(db, elemento_id, ubicacion_id)
    if stock is None:
        stock = models.StockUbicacion(
            elemento_id=elemento_id, ubicacion_id=ubicacion_id, cantidad=cantidad
        )
        db.add(stock)
    else:
        stock.cantidad = cantidad
    return stock


def aplicar_movimiento(
    db: Session,
    elemento: models.ElementoInventario,
    ubicacion_id: int,
    tipo: TipoMovimiento,
    cantidad: float,
    responsable_id: int | None,
    motivo: str | None,
    observaciones: str | None,
    fecha,
    hora=None,
):
    """Ajusta el stock del producto en la ubicación indicada y registra el movimiento."""
    c = Decimal(str(cantidad))
    stock = _obtener_stock(db, elemento.id, ubicacion_id)
    actual = Decimal(str(stock.cantidad)) if stock else Decimal("0")
    if tipo == TipoMovimiento.entrada:
        nuevo = actual + c
    else:
        if actual < c:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La salida es mayor a las existencias disponibles en esa ubicación",
            )
        nuevo = actual - c
    _crear_o_actualizar_stock(db, elemento.id, ubicacion_id, nuevo)

    movimiento = models.MovimientoInventario(
        elemento_id=elemento.id,
        ubicacion_id=ubicacion_id,
        tipo=tipo,
        cantidad=cantidad,
        responsable_id=responsable_id,
        motivo=motivo,
        observaciones=observaciones,
        fecha=fecha,
        hora=hora,
    )
    db.add(movimiento)
    return movimiento


def aplicar_traslado(
    db: Session,
    elemento: models.ElementoInventario,
    ubicacion_origen_id: int,
    ubicacion_destino_id: int,
    cantidad: float,
    responsable_id: int | None,
    observaciones: str | None,
    fecha,
    hora=None,
):
    """Mueve `cantidad` unidades del MISMO producto de una ubicación a otra.

    Registra la salida en el origen y la entrada en el destino como movimientos
    y guarda el traslado para trazabilidad.
    """
    c = Decimal(str(cantidad))
    stock_origen = _obtener_stock(db, elemento.id, ubicacion_origen_id)
    disponible = Decimal(str(stock_origen.cantidad)) if stock_origen else Decimal("0")
    if c > disponible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock insuficiente en origen para '{elemento.nombre}': disponible {disponible}, requerido {c}",
        )
    _crear_o_actualizar_stock(db, elemento.id, ubicacion_origen_id, disponible - c)
    stock_destino = _obtener_stock(db, elemento.id, ubicacion_destino_id)
    destino_actual = Decimal(str(stock_destino.cantidad)) if stock_destino else Decimal("0")
    _crear_o_actualizar_stock(db, elemento.id, ubicacion_destino_id, destino_actual + c)

    mov_origen = models.MovimientoInventario(
        elemento_id=elemento.id,
        ubicacion_id=ubicacion_origen_id,
        tipo=TipoMovimiento.salida,
        cantidad=cantidad,
        responsable_id=responsable_id,
        motivo="Traslado",
        observaciones=observaciones,
        fecha=fecha,
        hora=hora,
    )
    mov_destino = models.MovimientoInventario(
        elemento_id=elemento.id,
        ubicacion_id=ubicacion_destino_id,
        tipo=TipoMovimiento.entrada,
        cantidad=cantidad,
        responsable_id=responsable_id,
        motivo="Traslado",
        observaciones=observaciones,
        fecha=fecha,
        hora=hora,
    )
    db.add_all([mov_origen, mov_destino])
    traslado = models.Traslado(
        elemento_id=elemento.id,
        ubicacion_origen_id=ubicacion_origen_id,
        ubicacion_destino_id=ubicacion_destino_id,
        cantidad=cantidad,
        responsable_id=responsable_id,
        observaciones=observaciones,
        fecha=fecha,
        hora=hora,
    )
    db.add(traslado)
    return traslado


def alertas(db: Session, ubicacion_id: int | None = None):
    """Existencias (por ubicación) por debajo de su mínimo configurado (RF-18)."""
    cats = {c.id: c.nombre for c in db.execute(select(models.CategoriaInventario)).scalars().all()}
    ubs = {u.id: u.nombre for u in db.execute(select(models.Ubicacion)).scalars().all()}
    conds = [
        models.ElementoInventario.estado == models.EstadoRegistro.activo,
        models.ElementoInventario.minimo.isnot(None),
        models.StockUbicacion.cantidad <= models.ElementoInventario.minimo,
    ]
    if ubicacion_id is not None:
        conds.append(models.StockUbicacion.ubicacion_id == ubicacion_id)
    stmt = (
        select(models.StockUbicacion)
        .join(models.ElementoInventario, models.StockUbicacion.elemento_id == models.ElementoInventario.id)
        .where(*conds)
    )
    resultado = []
    for s in db.execute(stmt).scalars().all():
        e = s.elemento if hasattr(s, "elemento") else db.get(models.ElementoInventario, s.elemento_id)
        resultado.append({
            "tipo": "Elemento",
            "id": e.id,
            "nombre": e.nombre,
            "categoria": cats.get(e.categoria_id, "—"),
            "ubicacion": ubs.get(s.ubicacion_id, "—"),
            "cantidad": s.cantidad,
            "minimo": e.minimo,
            "unidad": e.unidad,
        })
    return resultado
