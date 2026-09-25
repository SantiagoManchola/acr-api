"""Router de reportes con exportación CSV/XLSX/PDF (RF-20, RF-36, RF-54).

Cada módulo expone su propio reporte filtrable:
  /reportes/inventario?tipo=elementos|quimicos
  /reportes/micromedidores?tipo=lecturas|suscriptores|micromedidores
  /reportes/planta?tipo=mediciones|actividades|dosificaciones|horas|quimicos
Todos aceptan `formato` (csv|xlsx|pdf, por defecto csv) y los filtros del módulo.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select

from .. import models
from ..security import get_current_user, get_db, require_role
from ..services import inventario as svc_inv, planta as svc_planta, micromedidores as svc_mm
from ..services.common import condiciones_busqueda
from ..services.export import a_csv, a_xlsx, a_pdf

router = APIRouter(prefix="/reportes", tags=["Reportes"])
# Roles por módulo (coherentes con el acceso a cada vista del CMS):
# - inventario: admin + administrativo
# - micromedidores: admin + administrativo + fontanero
# - planta: admin + operario (el administrativo NO tiene acceso a planta)
_LECTORES_INV = ["admin", "administrativo", "operario"]
_LECTORES_MM = ["admin", "administrativo", "fontanero"]
_LECTORES_PLANTA = ["admin", "operario"]


def _responder(filas, columnas, formato: str, nombre: str, titulo: str):
    formato = (formato or "csv").lower()
    if formato == "json":
        return filas
    if formato == "csv":
        return Response(content=a_csv(filas, columnas), media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={nombre}.csv"})
    if formato == "xlsx":
        return Response(content=a_xlsx(filas, columnas),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={nombre}.xlsx"})
    if formato == "pdf":
        return Response(content=a_pdf(filas, columnas, titulo), media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename={nombre}.pdf"})
    raise HTTPException(400, "formato debe ser csv, xlsx o pdf")


def _stock_resumen(db, e, ubicacion_id: int | None = None):
    """Total de existencias y detalle por ubicación de un elemento de inventario.

    Con ubicacion_id solo cuenta esa ubicación (alcance Oficina del administrativo).
    """
    ubs = {u.id: u.nombre for u in db.execute(select(models.Ubicacion)).scalars().all()}
    stmt = select(models.StockUbicacion).where(models.StockUbicacion.elemento_id == e.id)
    if ubicacion_id is not None:
        stmt = stmt.where(models.StockUbicacion.ubicacion_id == ubicacion_id)
    rows = db.execute(stmt).scalars().all()
    total = sum((Decimal(str(s.cantidad)) for s in rows), Decimal("0"))
    detalle = ", ".join(f"{ubs.get(s.ubicacion_id, s.ubicacion_id)}: {s.cantidad}" for s in rows) or "—"
    return total, detalle


def _oficina_scope(db, usuario) -> int | None:
    """Ubicación forzada para el administrativo (None = sin restricción).

    Devuelve -1 si el administrativo no tiene Oficina configurada (sin datos).
    """
    rol = usuario.rol.nombre if usuario.rol else ""
    if rol != "administrativo":
        return None
    ofi = svc_inv.ubicacion_por_nombre(db, "oficina")
    return ofi.id if ofi else -1


# ----------------------------- INVENTARIO -----------------------------------
@router.get("/inventario")
def reporte_inventario(
    tipo: str = Query(default="elementos"),
    nombre: str | None = None,
    categoria_id: int | None = None,
    estado: str | None = None,
    solo_insumos: bool = Query(default=False),
    formato: str = Query(default="csv"),
    db=Depends(get_db), usuario=Depends(require_role(_LECTORES_INV)),
):
    cats = {c.id: c for c in db.execute(select(models.CategoriaInventario)).scalars().all()}
    # El administrativo solo ve inventario de la Oficina (también en reportes).
    scope_ofi = _oficina_scope(db, usuario)

    def _stock(e):
        return _stock_resumen(db, e, ubicacion_id=scope_ofi if scope_ofi != -1 else None)

    def _tiene_stock(e):
        if scope_ofi is None:
            return True
        if scope_ofi == -1:
            return False
        return db.execute(
            select(models.StockUbicacion).where(
                models.StockUbicacion.elemento_id == e.id,
                models.StockUbicacion.ubicacion_id == scope_ofi,
            )
        ).scalars().first() is not None

    if tipo == "quimicos" or solo_insumos:
        # Los químicos son elementos de inventario cuya categoría es de tipo 'insumo'
        # y cuyo nombre identifica la sub-categoría "Químicos" (no todo insumo lo es).
        stmt = select(models.ElementoInventario).join(
            models.CategoriaInventario,
            models.ElementoInventario.categoria_id == models.CategoriaInventario.id,
        ).where(
            models.CategoriaInventario.tipo == models.CategoriaTipo.insumo,
            models.CategoriaInventario.nombre.ilike("%quimic%"),
        )
        filas = db.execute(stmt.order_by(models.ElementoInventario.nombre)).scalars().all()
        datos = []
        for e in filas:
            if not _tiene_stock(e):
                continue
            total, detalle = _stock(e)
            datos.append({
                "nombre": e.nombre,
                "categoria": cats.get(e.categoria_id).nombre,
                "unidad": e.unidad or "", "cantidad": total, "minimo": e.minimo,
                "ubicacion": detalle, "estado": e.estado,
            })
        columnas = ["nombre", "categoria", "unidad", "cantidad", "minimo", "ubicacion", "estado"]
        return _responder(datos, columnas, formato, "reporte_insumos", "Insumos/Químicos ACR")

    stmt = select(models.ElementoInventario)
    if nombre:
        stmt = stmt.where(*condiciones_busqueda(models.ElementoInventario.nombre, nombre))
    if categoria_id:
        stmt = stmt.where(models.ElementoInventario.categoria_id == categoria_id)
    if estado:
        stmt = stmt.where(models.ElementoInventario.estado == estado)
    filas = db.execute(stmt.order_by(models.ElementoInventario.nombre)).scalars().all()
    datos = []
    for e in filas:
        if not _tiene_stock(e):
            continue
        total, detalle = _stock(e)
        datos.append({
            "tipo": "Elemento", "nombre": e.nombre,
            "categoria": cats.get(e.categoria_id).nombre if e.categoria_id in cats else "—",
            "ubicacion": detalle, "cantidad": total, "unidad": e.unidad or "",
            "minimo": e.minimo, "valor": e.valor, "estado": e.estado,
        })
    columnas = ["tipo", "nombre", "categoria", "ubicacion", "cantidad", "unidad", "minimo", "valor", "estado"]
    return _responder(datos, columnas, formato, "reporte_inventario", "Inventario ACR")


# ----------------------------- MICROMEDIDORES --------------------------------
@router.get("/micromedidores")
def reporte_micromedidores(
    tipo: str = Query(default="lecturas"),
    nombre: str | None = None,
    identificacion: str | None = None,
    serial: str | None = None,
    condicion: str | None = None,
    sector: str | None = None,
    tipo_usuario: str | None = None,
    micromedidor_id: int | None = None,
    suscriptor_id: int | None = None,
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    orden: str = Query(default="suscriptor"),
    dir_orden: str = Query(default="asc"),
    formato: str = Query(default="csv"),
    db=Depends(get_db), _=Depends(require_role(_LECTORES_MM)),
):
    if tipo == "suscriptores":
        filas, _ = svc_mm.filtrar_suscriptores(
            db, nombre=nombre, identificacion=identificacion, sector=sector, tipo_usuario=tipo_usuario)
        datos = [{"nombre": s.nombre, "codigo_usuario": s.codigo_usuario or "",
                  "codigo_facturacion": s.codigo_facturacion or "",
                  "identificacion": s.identificacion or "",
                  "sector": s.sector or "", "tipo_usuario": s.tipo_usuario,
                  "direccion": s.direccion or ""} for s in filas]
        columnas = ["nombre", "codigo_usuario", "codigo_facturacion", "identificacion",
                    "sector", "tipo_usuario", "direccion"]
        return _responder(datos, columnas, formato, "reporte_suscriptores", "Suscriptores ACR")

    if tipo == "micromedidores":
        # Orden configurable para imprimir: por suscriptor (medidores de un
        # mismo suscriptor agrupados) o por serial, en asc/desc.
        if orden not in ("suscriptor", "serial"):
            raise HTTPException(400, "orden debe ser 'suscriptor' o 'serial'")
        if (dir_orden or "").lower() not in ("asc", "desc"):
            raise HTTPException(400, "dir_orden debe ser 'asc' o 'desc'")
        filas, _ = svc_mm.filtrar_micromedidores(
            db, serial=serial or nombre, sector=sector, condicion=condicion,
            suscriptor_id=suscriptor_id, orden=orden, dir_orden=dir_orden,
        )
        datos = [{"suscriptor": m["suscriptor_nombre"] or "—",
                  "serial": m["serial"], "condicion": m["condicion"],
                  "tipo": m["tipo"] or "", "direccion": m["direccion"] or "",
                  "fecha_instalacion": m["fecha_instalacion"]} for m in filas]
        columnas = ["suscriptor", "serial", "condicion", "tipo",
                    "direccion", "fecha_instalacion"]
        return _responder(datos, columnas, formato, "reporte_micromedidores", "Micromedidores ACR")

    # lecturas (consumo)
    filas, _ = svc_mm.filtrar_lecturas(db, micromedidor_id=micromedidor_id, suscriptor_id=suscriptor_id,
                                       sector=sector, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
    datos = [{"fecha": l["fecha"], "hora": l["hora"],
              "suscriptor": l["suscriptor_nombre"] or l["suscriptor_id"],
              "medidor": l["medidor_serial"] or l["micromedidor_id"],
              "lectura": l["lectura"], "consumo": l["consumo"],
              "promedio_usado": l["promedio_usado"], "irregular": l["irregular"], "novedad": l["novedad"] or "",
              "foto_url": l["foto_url"] or ""}
             for l in filas]
    columnas = ["fecha", "hora", "suscriptor", "medidor", "lectura", "consumo", "promedio_usado", "irregular", "novedad", "foto_url"]
    return _responder(datos, columnas, formato, "reporte_consumo", "Consumo micromedidores ACR")


# ----------------------------- PLANTA ----------------------------------------
@router.get("/planta")
def reporte_planta(
    tipo: str = Query(default="mediciones"),
    parametro_id: int | None = None,
    fuera_rango: bool | None = None,
    elemento_id: int | None = None,
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    formato: str = Query(default="csv"),
    db=Depends(get_db), _=Depends(require_role(_LECTORES_PLANTA)),
):
    if tipo == "quimicos":
        # Químicos = elementos de inventario de categoría tipo 'insumo' y nombre "Químicos".
        stmt = select(models.ElementoInventario).join(
            models.CategoriaInventario,
            models.ElementoInventario.categoria_id == models.CategoriaInventario.id,
        ).where(
            models.CategoriaInventario.tipo == models.CategoriaTipo.insumo,
            models.CategoriaInventario.nombre.ilike("%quimic%"),
        )
        filas = db.execute(stmt.order_by(models.ElementoInventario.nombre)).scalars().all()
        datos = []
        for e in filas:
            total, _ = _stock_resumen(db, e)
            datos.append({"nombre": e.nombre, "unidad": e.unidad or "",
                          "cantidad": total, "minimo": e.minimo})
        columnas = ["nombre", "unidad", "cantidad", "minimo"]
        return _responder(datos, columnas, formato, "reporte_insumos_planta", "Insumos/Químicos Planta ACR")

    if tipo == "actividades":
        filas, _ = svc_planta.filtrar_actividades(db, tipo=None, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
        datos = [{"tipo": a["tipo"], "fecha": a["fecha"], "hora": a["hora"],
                  "responsable": a["responsable_nombre"] or "—",
                  "observaciones": a["observaciones"] or "", "foto_url": a["foto_url"] or ""} for a in filas]
        columnas = ["tipo", "fecha", "hora", "responsable", "observaciones", "foto_url"]
        return _responder(datos, columnas, formato, "reporte_actividades", "Actividades Planta ACR")

    if tipo == "dosificaciones":
        filas, _ = svc_planta.filtrar_dosificaciones(db, elemento_id=elemento_id, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
        datos = [{"fecha": d["fecha"], "hora": d["hora"],
                  "insumo": d["elemento_nombre"] or d["elemento_id"],
                  "cantidad": d["cantidad"], "unidad": d["unidad"] or "",
                  "tasa": d["tasa"], "unidad_tasa": d["unidad_tasa"] or "",
                  "observaciones": d["observaciones"] or ""} for d in filas]
        columnas = ["fecha", "hora", "insumo", "cantidad", "unidad", "tasa", "unidad_tasa", "observaciones"]
        return _responder(datos, columnas, formato, "reporte_dosificaciones", "Dosificaciones ACR")

    if tipo == "horas":
        filas, _ = svc_planta.filtrar_horas(db, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
        datos = [{"fecha": h["fecha"], "horas": h["horas"],
                  "responsable": h["responsable_nombre"] or "—",
                  "observaciones": h["observaciones"] or ""} for h in filas]
        columnas = ["fecha", "horas", "responsable", "observaciones"]
        return _responder(datos, columnas, formato, "reporte_horas", "Horas de servicio ACR")

    # mediciones (por defecto)
    filas, _ = svc_planta.filtrar_mediciones(db, parametro_id=parametro_id, fuera_rango=fuera_rango,
                                             fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
    datos = [{"fecha": m["fecha"], "hora": m["hora"], "parametro": m["parametro_nombre"] or m["parametro_id"],
              "valor": m["valor"], "fuera_rango": m["fuera_rango"],
              "accion_correctiva": m["accion_correctiva"] or "", "foto_url": m["foto_url"] or ""} for m in filas]
    columnas = ["fecha", "hora", "parametro", "valor", "fuera_rango", "accion_correctiva", "foto_url"]
    return _responder(datos, columnas, formato, "reporte_planta", "Planta de tratamiento ACR")
