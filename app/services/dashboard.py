"""Resumen ligero para el dashboard: agregados SQL en una sola petición.

Evita que el CMS descargue listas completas (suscriptores, lecturas,
elementos...) solo para pintar KPIs y tops: cada bloque se calcula en la
API con COUNT/SUM/AVG y límites, y el payload respeta el rol del usuario.
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .. import models
from . import inventario as svc_inv, planta as svc_planta


def ubicacion_id(db: Session, patron: str) -> int | None:
    ubs = svc_inv.ubicacion_por_nombre(db, patron)
    return ubs.id if ubs else None


def _elementos_activo(db: Session) -> int:
    return int(db.execute(
        select(func.count(models.ElementoInventario.id)).where(
            models.ElementoInventario.estado == models.EstadoRegistro.activo
        )
    ).scalar_one())


def _elementos_en_oficina(db: Session, oficina_id: int) -> int:
    return int(db.execute(
        select(func.count(func.distinct(models.StockUbicacion.elemento_id))).where(
            models.StockUbicacion.ubicacion_id == oficina_id,
        )
    ).scalar_one())


def _alertas_count(db: Session, oficina_id: int | None) -> int:
    conds = [
        models.ElementoInventario.estado == models.EstadoRegistro.activo,
        models.ElementoInventario.minimo.isnot(None),
        models.StockUbicacion.cantidad <= models.ElementoInventario.minimo,
    ]
    if oficina_id is not None:
        conds.append(models.StockUbicacion.ubicacion_id == oficina_id)
    return int(db.execute(
        select(func.count(models.StockUbicacion.id))
        .join(models.ElementoInventario, models.StockUbicacion.elemento_id == models.ElementoInventario.id)
        .where(*conds)
    ).scalar_one())


def resumen_inventario(db: Session, oficina_id: int | None = None) -> dict:
    """KPIs de inventario (conteos server-side, sin traer listas)."""
    if oficina_id is not None:
        elementos = _elementos_en_oficina(db, oficina_id)
    else:
        elementos = _elementos_activo(db)
    return {
        "elementos": elementos,
        "alertas": _alertas_count(db, oficina_id),
        "solo_oficina": oficina_id is not None,
    }


def _quimicos_stmt_planta(db: Session):
    """Químicos con stock EN planta (categoría tipo insumo llamada *Químicos*)."""
    planta = svc_inv.ubicacion_por_nombre(db, "planta de tratamiento", "planta")
    if planta is None:
        return None, None
    stmt = (
        select(
            models.ElementoInventario.id,
            models.ElementoInventario.nombre,
            models.ElementoInventario.unidad,
            models.ElementoInventario.minimo,
            models.StockUbicacion.cantidad,
        )
        .join(
            models.CategoriaInventario,
            models.ElementoInventario.categoria_id == models.CategoriaInventario.id,
        )
        .join(
            models.StockUbicacion,
            models.StockUbicacion.elemento_id == models.ElementoInventario.id,
        )
        .where(
            models.ElementoInventario.estado == models.EstadoRegistro.activo,
            models.CategoriaInventario.tipo == models.CategoriaTipo.insumo,
            models.CategoriaInventario.nombre.ilike("%quimic%"),
            models.StockUbicacion.ubicacion_id == planta.id,
        )
    )
    return stmt, planta.id


def quimicos_planta(db: Session, limite: int = 6) -> dict:
    """Químicos en planta: total, cuántos bajo mínimo y top por criticidad.

    Orden: bajos de mínimo primero, luego menor cantidad absoluta.
    """
    stmt, _ = _quimicos_stmt_planta(db)
    if stmt is None:
        return {"total": 0, "bajos": 0, "items": []}

    minimo = models.ElementoInventario.minimo
    cantidad = models.StockUbicacion.cantidad
    stmt = stmt.order_by(
        case((minimo.is_(None), 1), else_=case((cantidad <= minimo, 0), else_=1)),
        cantidad.asc(),
    )
    base = stmt.order_by(None).subquery()
    total = int(db.execute(select(func.count()).select_from(base)).scalar_one())
    bajos_stmt = (
        stmt.where(
            minimo.isnot(None),
            cantidad <= minimo,
        )
        .order_by(None)
        .subquery()
    )
    bajos = int(db.execute(select(func.count()).select_from(bajos_stmt)).scalar_one())
    filas = db.execute(stmt.limit(limite)).all()
    items = [
        {"id": f_id, "nombre": nombre, "unidad": unidad, "cantidad": cant, "minimo": min}
        for (f_id, nombre, unidad, min, cant) in filas
    ]
    return {"total": total, "bajos": bajos, "items": items}


def resumen_micromedicion(db: Session, dias: int, ver_medidores: bool, ver_consumo: bool) -> dict:
    """KPIs de micromedición; consumo agregado solo cuando el rol lo ve."""
    out = {"suscriptores": 0, "medidores": 0, "frenados_total": 0, "frenados": [], "consumo": None}
    if ver_medidores:
        out["suscriptores"] = int(db.execute(
            select(func.count(models.Suscriptor.id))
        ).scalar_one())
        out["medidores"] = int(db.execute(
            select(func.count(models.Micromedidor.id))
        ).scalar_one())
        frenados_total = int(db.execute(
            select(func.count(models.Micromedidor.id)).where(
                models.Micromedidor.condicion == models.CondicionMedidor.frenado
            )
        ).scalar_one())
        out["frenados_total"] = frenados_total
        if frenados_total:
            filas = db.execute(
                select(
                    models.Micromedidor.id,
                    models.Micromedidor.serial,
                    models.Suscriptor.nombre,
                    models.Suscriptor.sector,
                )
                .outerjoin(models.Suscriptor, models.Micromedidor.suscriptor_id == models.Suscriptor.id)
                .where(models.Micromedidor.condicion == models.CondicionMedidor.frenado)
                .order_by(models.Micromedidor.serial)
                .limit(5)
            ).all()
            out["frenados"] = [
                {"id": i, "serial": s, "suscriptor": n, "sector": sec}
                for (i, s, n, sec) in filas
            ]
    if ver_consumo:
        hoy = date.today()
        desde = hoy - timedelta(days=dias - 1)
        base = select(models.Lectura.consumo).where(
            models.Lectura.fecha >= desde,
            models.Lectura.consumo.isnot(None),
        )
        agg = db.execute(
            select(
                func.sum(models.Lectura.consumo),
                func.avg(models.Lectura.consumo),
                func.count(models.Lectura.id),
            ).where(
                models.Lectura.fecha >= desde,
                models.Lectura.consumo.isnot(None),
            )
        ).one()
        total, promedio, lecturas = agg
        por_medidor = db.execute(
            select(
                models.Lectura.micromedidor_id,
                models.Micromedidor.serial,
                models.Suscriptor.nombre,
                models.Suscriptor.sector,
                func.sum(models.Lectura.consumo),
                func.avg(models.Lectura.consumo),
                func.count(models.Lectura.id),
            )
            .join(models.Micromedidor, models.Lectura.micromedidor_id == models.Micromedidor.id)
            .outerjoin(models.Suscriptor, models.Micromedidor.suscriptor_id == models.Suscriptor.id)
            .where(
                models.Lectura.fecha >= desde,
                models.Lectura.consumo.isnot(None),
            )
            .group_by(
                models.Lectura.micromedidor_id,
                models.Micromedidor.serial,
                models.Suscriptor.nombre,
                models.Suscriptor.sector,
            )
            .order_by(func.sum(models.Lectura.consumo).desc())
            .limit(8)
        ).all()
        out["consumo"] = {
            "dias": dias,
            "desde": desde,
            "hasta": hoy,
            "total": total if total is not None else Decimal("0"),
            "promedio": promedio,
            "lecturas": int(lecturas or 0),
            "por_medidor": [
                {
                    "micromedidor_id": mid,
                    "serial": serial,
                    "suscriptor": nombre,
                    "sector": sector,
                    "total": t,
                    "promedio": p,
                    "lecturas": int(n or 0),
                }
                for (mid, serial, nombre, sector, t, p, n) in por_medidor
            ],
        }
    return out
