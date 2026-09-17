"""Router de inventario (RF-06..RF-20)."""
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from .. import models
from ..schemas import (
    AlertaOut,
    CategoriaCreate,
    CategoriaOut,
    ElementoCreate,
    ElementoOpcionOut,
    ElementoOut,
    ElementosPagina,
    ElementoUpdate,
    MovimientoCreate,
    MovimientoOut,
    MovimientosPagina,
    StockUbicacionOut,
    TipoMovimiento,
    TrasladoCreate,
    TrasladoOut,
    TrasladosPagina,
    UbicacionCreate,
    UbicacionOut,
    UbicacionUpdate,
)
from ..security import get_current_user, get_db, require_role
from ..services.common import condiciones_busqueda, sellar, como_pagina, orden_validado
from ..services import inventario as svc_inventario

router = APIRouter(prefix="/inventario", tags=["Inventario"])

_LECTORES = ["admin", "administrativo", "operario"]
_ESCRITORES = ["admin", "administrativo"]
# CRUD de ubicaciones: SOLO admin (el administrativo no gestiona ubicaciones).
_SOLO_ADMIN = ["admin"]
# El operario NO crea ni edita fichas: solo registra ENTRADAS (ingresos) de
# químicos YA EXISTENTES en la planta (POST /{id}/entrada).
_OPERARIO_QUIMICOS = ["admin", "administrativo", "operario"]


def _rol(usuario: models.Usuario) -> str:
    return usuario.rol.nombre if usuario.rol else ""


def _ubicacion_por_nombre(db: Session, patron: str):
    """Ubicación por nombre: exacta primero ('Planta de tratamiento' antes que
    'Planta'; 'Oficina' antes que 'Nueva oficina'). None si no existe."""
    if patron.lower() == "planta":
        return svc_inventario.ubicacion_por_nombre(db, "planta de tratamiento", "planta")
    if patron.lower() == "oficina":
        return svc_inventario.ubicacion_por_nombre(db, "oficina")
    return svc_inventario.ubicacion_por_nombre(db, patron)


def _es_quimico(db: Session, categoria_id: int | None) -> bool:
    """Un químico es un elemento de categoría tipo 'insumo' llamada *quimic*."""
    if not categoria_id:
        return False
    cat = db.get(models.CategoriaInventario, categoria_id)
    return bool(
        cat
        and cat.tipo == models.CategoriaTipo.insumo
        and "quimic" in (cat.nombre or "").lower()
    )


def _oficina_id(db: Session) -> int | None:
    """Id de la ubicación Oficina (None si no existe)."""
    ofi = _ubicacion_por_nombre(db, "oficina")
    return ofi.id if ofi else None


def _solo_oficina(usuario: models.Usuario) -> bool:
    """El administrativo SOLO ve inventario de la ubicación Oficina."""
    return _rol(usuario) == "administrativo"


def _exigir_quimico_planta(db: Session, usuario: models.Usuario, categoria_id: int | None, ubicacion_id: int | None):
    """El operario solo ingresa químicos EXISTENTES en la planta.

    Solo se usa en entradas de stock: el químico ya debe existir y el
    movimiento debe ser EN planta. Admin/administrativo pasan sin restricción.
    Lanza 403 si no cumple. (Crear/editar fichas: solo admin/administrativo.)
    """
    if _rol(usuario) != "operario":
        return
    if not _es_quimico(db, categoria_id):
        raise HTTPException(403, "El operario solo puede ingresar químicos (categoría de insumos *Químicos*)")
    planta = _ubicacion_por_nombre(db, "planta")
    if not planta or ubicacion_id != planta.id:
        raise HTTPException(403, "El operario solo puede registrar químicos en la ubicación Planta de tratamiento")


# ----------------------------- Helpers de salida -------------------------------
def _mapa_ubicaciones(db: Session) -> dict:
    return {u.id: u.nombre for u in db.execute(select(models.Ubicacion)).scalars().all()}


def _mapa_usuarios(db: Session) -> dict:
    return {u.id: u.nombre for u in db.execute(select(models.Usuario)).scalars().all()}


def _mapa_elementos(db: Session) -> dict:
    return {e.id: e.nombre for e in db.execute(
        select(models.ElementoInventario.id, models.ElementoInventario.nombre)
    ).all()}


def _stock_elemento(db: Session, elemento_id: int, ubs: dict):
    rows = db.execute(
        select(models.StockUbicacion).where(models.StockUbicacion.elemento_id == elemento_id)
    ).scalars().all()
    stock = [
        StockUbicacionOut(
            id=s.id, elemento_id=s.elemento_id, ubicacion_id=s.ubicacion_id,
            ubicacion=ubs.get(s.ubicacion_id), cantidad=s.cantidad,
        )
        for s in rows
    ]
    total = sum((Decimal(str(s.cantidad)) for s in rows), Decimal("0"))
    return stock, total


def _elemento_out(db: Session, e: models.ElementoInventario, ubicacion_id: int | None = None,
                  solo_stock_ubicacion: int | None = None):
    ubs = _mapa_ubicaciones(db)
    stock, total = _stock_elemento(db, e.id, ubs)
    if solo_stock_ubicacion is not None:
        stock = [s for s in stock if s.ubicacion_id == solo_stock_ubicacion]
    if ubicacion_id:
        f = next((s for s in stock if s.ubicacion_id == ubicacion_id), None)
        cantidad = f.cantidad if f else Decimal("0")
    else:
        cantidad = total
    return ElementoOut(
        id=e.id, nombre=e.nombre, categoria_id=e.categoria_id, cantidad=cantidad,
        unidad=e.unidad, proveedor=e.proveedor, valor=e.valor, minimo=e.minimo,
        estado=e.estado, observaciones=e.observaciones, stock=stock,
    )


# ----------------------------- Categorías ------------------------------------
@router.get("/categorias", response_model=list[CategoriaOut], summary="Listar categorías")
def listar_categorias(
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(_LECTORES)),
):
    return db.execute(select(models.CategoriaInventario)).scalars().all()


@router.post(
    "/categorias",
    response_model=CategoriaOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear categoría",
)
def crear_categoria(
    payload: CategoriaCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    cat = models.CategoriaInventario(
        nombre=payload.nombre, tipo=payload.tipo, descripcion=payload.descripcion
    )
    sellar(cat, usuario, nuevo=True)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


# ----------------------------- Elementos (lista/crear) -----------------------
@router.get("", response_model=ElementosPagina, summary="Listar elementos (paginado)")
def listar_elementos(
    nombre: str | None = None,
    categoria_id: int | None = None,
    categoria_tipo: str | None = None,
    categoria_nombre: str | None = None,
    ubicacion_id: int | None = None,
    estado: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    orden: str | None = Query(default=None, description="Campo de orden (whitelist del servicio)"),
    dir_orden: str = Query(default="asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_LECTORES)),
):
    # El administrativo SOLO ve inventario de la Oficina: se fuerza el filtro
    # (se ignora cualquier otra ubicación pedida) y el stock de respuesta.
    oficina = _oficina_id(db) if _solo_oficina(usuario) else None
    if _solo_oficina(usuario):
        if oficina is None:
            return como_pagina([], 0, page, page_size)
        ubicacion_id = oficina
    stmt = select(models.ElementoInventario)
    con_join_cat = False
    if nombre:
        stmt = stmt.where(*condiciones_busqueda(models.ElementoInventario.nombre, nombre))
    if categoria_id:
        stmt = stmt.where(models.ElementoInventario.categoria_id == categoria_id)
    if categoria_tipo or categoria_nombre:
        stmt = stmt.join(
            models.CategoriaInventario,
            models.ElementoInventario.categoria_id == models.CategoriaInventario.id,
        )
        con_join_cat = True
    if categoria_tipo:
        stmt = stmt.where(models.CategoriaInventario.tipo == categoria_tipo)
    if categoria_nombre:
        stmt = stmt.where(models.CategoriaInventario.nombre.ilike(f"%{categoria_nombre}%"))
    if estado:
        stmt = stmt.where(models.ElementoInventario.estado == estado)
    if ubicacion_id:
        stmt = stmt.join(
            models.StockUbicacion,
            models.StockUbicacion.elemento_id == models.ElementoInventario.id,
        ).where(models.StockUbicacion.ubicacion_id == ubicacion_id)

    # Orden server-side (whitelist): la cantidad total se calcula post-query,
    # por eso NO es ordenable aquí (el CMS la marca no-ordenable).
    permitidos = {
        "nombre": models.ElementoInventario.nombre,
        "categoria": models.CategoriaInventario.nombre,
        "unidad": models.ElementoInventario.unidad,
        "minimo": models.ElementoInventario.minimo,
        "valor": models.ElementoInventario.valor,
        "estado": models.ElementoInventario.estado,
    }
    if orden == "categoria" and not con_join_cat:
        stmt = stmt.join(
            models.CategoriaInventario,
            models.ElementoInventario.categoria_id == models.CategoriaInventario.id,
        )
        con_join_cat = True
    por_defecto = lambda s: s.order_by(models.ElementoInventario.nombre)
    ordenar_fn = orden_validado(
        orden, dir_orden, permitidos, por_defecto, desempate=models.ElementoInventario.id.asc()
    )

    total = int(db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one())
    elementos = db.execute(
        ordenar_fn(stmt).limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()

    ubs = _mapa_ubicaciones(db)
    stock_por_elem: dict[int, list] = {}
    for s in db.execute(select(models.StockUbicacion)).scalars().all():
        stock_por_elem.setdefault(s.elemento_id, []).append(s)

    resultado = []
    for e in elementos:
        rows = stock_por_elem.get(e.id, [])
        if oficina is not None:
            rows = [s for s in rows if s.ubicacion_id == oficina]
        stock = [
            StockUbicacionOut(
                id=s.id, elemento_id=s.elemento_id, ubicacion_id=s.ubicacion_id,
                ubicacion=ubs.get(s.ubicacion_id), cantidad=s.cantidad,
            )
            for s in rows
        ]
        total_stock = sum((Decimal(str(s.cantidad)) for s in rows), Decimal("0"))
        cantidad = total_stock
        if ubicacion_id:
            f = next((s for s in stock if s.ubicacion_id == ubicacion_id), None)
            cantidad = f.cantidad if f else Decimal("0")
        resultado.append(
            ElementoOut(
                id=e.id, nombre=e.nombre, categoria_id=e.categoria_id, cantidad=cantidad,
                unidad=e.unidad, proveedor=e.proveedor, valor=e.valor, minimo=e.minimo,
                estado=e.estado, observaciones=e.observaciones, stock=stock,
            )
        )
    return como_pagina(resultado, total, page, page_size)


@router.post(
    "",
    response_model=ElementoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear elemento de inventario",
)
def crear_elemento(
    payload: ElementoCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    if not db.get(models.CategoriaInventario, payload.categoria_id):
        raise HTTPException(400, "categoria_id inválido")
    if payload.ubicacion_id and not db.get(models.Ubicacion, payload.ubicacion_id):
        raise HTTPException(400, "ubicacion_id inválido")
    elem = models.ElementoInventario(
        nombre=payload.nombre,
        categoria_id=payload.categoria_id,
        unidad=payload.unidad,
        proveedor=payload.proveedor,
        valor=payload.valor,
        minimo=payload.minimo,
        observaciones=payload.observaciones,
    )
    sellar(elem, usuario, nuevo=True)
    db.add(elem)
    db.flush()
    if payload.ubicacion_id and payload.cantidad_inicial is not None:
        db.add(models.StockUbicacion(
            elemento_id=elem.id, ubicacion_id=payload.ubicacion_id,
            cantidad=payload.cantidad_inicial,
        ))
    db.commit()
    db.refresh(elem)
    return _elemento_out(db, elem)


# ----------------------------- Ubicaciones ------------------------------------
@router.get("/ubicaciones", response_model=list[UbicacionOut], summary="Listar ubicaciones")
def listar_ubicaciones(
    db: Session = Depends(get_db),
    _: models.Usuario = Depends(require_role(_LECTORES)),
):
    # Directorio de lugares: visible para todos los lectores (solo VER).
    # El CRUD es SOLO admin y el stock visible se acota por rol en cada endpoint.
    return db.execute(select(models.Ubicacion).order_by(models.Ubicacion.nombre)).scalars().all()


@router.post(
    "/ubicaciones",
    response_model=UbicacionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear ubicación",
)
def crear_ubicacion(
    payload: UbicacionCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_SOLO_ADMIN)),
):
    ub = models.Ubicacion(**payload.model_dump())
    sellar(ub, usuario, nuevo=True)
    db.add(ub)
    db.commit()
    db.refresh(ub)
    return ub


@router.patch("/ubicaciones/{uid}", response_model=UbicacionOut, summary="Actualizar ubicación")
def actualizar_ubicacion(
    uid: int,
    payload: UbicacionUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_SOLO_ADMIN)),
):
    ub = db.get(models.Ubicacion, uid)
    if not ub:
        raise HTTPException(404, "Ubicación no encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(ub, k, v)
    sellar(ub, usuario, nuevo=False)
    db.commit()
    db.refresh(ub)
    return ub


# ----------------------------- Traslados ---------------------------------------
@router.get(
    "/opciones",
    response_model=list[ElementoOpcionOut],
    summary="Elementos ligeros para selects (con stock resumido)",
)
def opciones_elementos(
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_LECTORES)),
):
    oficina = _oficina_id(db) if _solo_oficina(usuario) else None
    ubs = _mapa_ubicaciones(db)
    stock_por_elem: dict[int, list] = {}
    for s in db.execute(select(models.StockUbicacion)).scalars().all():
        stock_por_elem.setdefault(s.elemento_id, []).append(s)
    resultado = []
    for e in db.execute(
        select(models.ElementoInventario).order_by(models.ElementoInventario.nombre)
    ).scalars().all():
        rows = stock_por_elem.get(e.id, [])
        if oficina is not None:
            rows = [s for s in rows if s.ubicacion_id == oficina]
        stock = [
            StockUbicacionOut(
                id=s.id, elemento_id=s.elemento_id, ubicacion_id=s.ubicacion_id,
                ubicacion=ubs.get(s.ubicacion_id), cantidad=s.cantidad,
            )
            for s in rows
        ]
        resultado.append(
            ElementoOpcionOut(
                id=e.id, nombre=e.nombre, categoria_id=e.categoria_id,
                unidad=e.unidad, estado=e.estado, stock=stock,
            )
        )
    return resultado


@router.get("/traslados", response_model=TrasladosPagina, summary="Listar traslados (paginado)")
def listar_traslados(
    elemento_id: int | None = None,
    ubicacion_origen_id: int | None = None,
    ubicacion_destino_id: int | None = None,
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    orden: str | None = Query(default=None, description="Campo de orden (whitelist del servicio)"),
    dir_orden: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_LECTORES)),
):
    # El administrativo ve los traslados donde participa la Oficina
    # (origen o destino), ignorando otros filtros de ubicación.
    if _solo_oficina(usuario):
        oficina = _oficina_id(db)
        if oficina is None:
            return como_pagina([], 0, page, page_size)
        ubicacion_origen_id = ubicacion_destino_id = None
        stmt = select(models.Traslado).where(
            or_(
                models.Traslado.ubicacion_origen_id == oficina,
                models.Traslado.ubicacion_destino_id == oficina,
            )
        )
    else:
        stmt = select(models.Traslado)
    if elemento_id:
        stmt = stmt.where(models.Traslado.elemento_id == elemento_id)
    if ubicacion_origen_id:
        stmt = stmt.where(models.Traslado.ubicacion_origen_id == ubicacion_origen_id)
    if ubicacion_destino_id:
        stmt = stmt.where(models.Traslado.ubicacion_destino_id == ubicacion_destino_id)
    if fecha_inicio:
        stmt = stmt.where(models.Traslado.fecha >= fecha_inicio)
    if fecha_fin:
        stmt = stmt.where(models.Traslado.fecha <= fecha_fin)
    ubs = _mapa_ubicaciones(db)
    usuarios = _mapa_usuarios(db)
    elementos = _mapa_elementos(db)

    # Orden server-side con alias para origen/destino (misma tabla, dos joins).
    origen_u = aliased(models.Ubicacion)
    destino_u = aliased(models.Ubicacion)
    permitidos = {
        "fecha": models.Traslado.fecha,
        "hora": models.Traslado.hora,
        "elemento": models.ElementoInventario.nombre,
        "origen": origen_u.nombre,
        "destino": destino_u.nombre,
        "cantidad": models.Traslado.cantidad,
        "responsable": models.Usuario.nombre,
    }
    if orden == "elemento":
        stmt = stmt.join(
            models.ElementoInventario,
            models.Traslado.elemento_id == models.ElementoInventario.id,
        )
    if orden == "origen":
        stmt = stmt.join(origen_u, models.Traslado.ubicacion_origen_id == origen_u.id)
    if orden == "destino":
        stmt = stmt.join(destino_u, models.Traslado.ubicacion_destino_id == destino_u.id)
    if orden == "responsable":
        stmt = stmt.join(models.Usuario, models.Traslado.responsable_id == models.Usuario.id)
    por_defecto = lambda s: s.order_by(
        models.Traslado.fecha.desc(), models.Traslado.hora.desc(), models.Traslado.id.desc()
    )
    ordenar_fn = orden_validado(
        orden, dir_orden, permitidos, por_defecto, desempate=models.Traslado.id.desc()
    )
    total = int(db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one())
    filas = db.execute(
        ordenar_fn(stmt).limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()
    items = [
        TrasladoOut(
            id=t.id, elemento_id=t.elemento_id,
            elemento_nombre=elementos.get(t.elemento_id),
            ubicacion_origen_id=t.ubicacion_origen_id, ubicacion_destino_id=t.ubicacion_destino_id,
            ubicacion_origen=ubs.get(t.ubicacion_origen_id),
            ubicacion_destino=ubs.get(t.ubicacion_destino_id),
            cantidad=t.cantidad, responsable_id=t.responsable_id,
            responsable_nombre=usuarios.get(t.responsable_id),
            observaciones=t.observaciones, fecha=t.fecha, hora=t.hora,
        )
        for t in filas
    ]
    return como_pagina(items, total, page, page_size)


@router.post(
    "/traslados",
    response_model=TrasladoOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar traslado entre ubicaciones (mismo producto)",
)
def crear_traslado(
    payload: TrasladoCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    elemento = db.get(models.ElementoInventario, payload.elemento_id)
    if not elemento:
        raise HTTPException(400, "elemento_id inválido")
    if not db.get(models.Ubicacion, payload.ubicacion_origen_id):
        raise HTTPException(400, "ubicacion_origen_id inválida")
    if not db.get(models.Ubicacion, payload.ubicacion_destino_id):
        raise HTTPException(400, "ubicacion_destino_id inválida")
    if payload.ubicacion_origen_id == payload.ubicacion_destino_id:
        raise HTTPException(400, "El origen y el destino deben ser distintos")
    # El administrativo sí puede trasladar, pero solo SACA stock de la Oficina
    # (su alcance): el destino puede ser cualquier ubicación visible.
    if _solo_oficina(usuario):
        oficina = _oficina_id(db)
        if oficina is None or payload.ubicacion_origen_id != oficina:
            raise HTTPException(403, "Solo puede trasladar stock desde la ubicación Oficina")
    fecha = payload.fecha or date.today()
    hora = payload.hora or datetime.now().time()
    traslado = svc_inventario.aplicar_traslado(
        db, elemento, payload.ubicacion_origen_id, payload.ubicacion_destino_id,
        payload.cantidad, usuario.id, payload.observaciones, fecha, hora,
    )
    sellar(traslado, usuario, nuevo=True)
    db.commit()
    db.refresh(traslado)
    ubs = _mapa_ubicaciones(db)
    return TrasladoOut(
        id=traslado.id, elemento_id=traslado.elemento_id,
        ubicacion_origen_id=traslado.ubicacion_origen_id, ubicacion_destino_id=traslado.ubicacion_destino_id,
        ubicacion_origen=ubs.get(traslado.ubicacion_origen_id),
        ubicacion_destino=ubs.get(traslado.ubicacion_destino_id),
        cantidad=traslado.cantidad, responsable_id=traslado.responsable_id,
        observaciones=traslado.observaciones, fecha=traslado.fecha, hora=traslado.hora,
    )


# ----------------------------- Stock por ubicación ----------------------------
@router.delete("/stock/{stock_id}", summary="Quitar producto de una ubicación")
def eliminar_stock(
    stock_id: int,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    """Elimina la existencia del producto en una ubicación concreta.

    Solo se permite cuando el stock es 0 (el producto ya no se procesa allí).
    Si tiene stock, debe trasladarse o registrarse salida primero.
    """
    s = db.get(models.StockUbicacion, stock_id)
    if not s:
        raise HTTPException(404, "Registro de stock no encontrado")
    if Decimal(str(s.cantidad)) > 0:
        raise HTTPException(400, "Solo se puede quitar una ubicación con stock en 0")
    db.delete(s)
    db.commit()
    return {"ok": True, "mensaje": "Producto quitado de la ubicación"}


# ----------------------------- Movimientos y alertas (literales) -------------
@router.get("/movimientos", response_model=MovimientosPagina, summary="Historial de movimientos (paginado)")
def movimientos(
    elemento_id: int | None = None,
    ubicacion_id: int | None = None,
    tipo: str | None = None,
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    orden: str | None = Query(default=None, description="Campo de orden (whitelist del servicio)"),
    dir_orden: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_LECTORES)),
):
    # El administrativo solo ve movimientos de la Oficina.
    if _solo_oficina(usuario):
        oficina = _oficina_id(db)
        if oficina is None:
            return como_pagina([], 0, page, page_size)
        ubicacion_id = oficina
    stmt = select(models.MovimientoInventario)
    if elemento_id:
        stmt = stmt.where(models.MovimientoInventario.elemento_id == elemento_id)
    if ubicacion_id:
        stmt = stmt.where(models.MovimientoInventario.ubicacion_id == ubicacion_id)
    if tipo:
        stmt = stmt.where(models.MovimientoInventario.tipo == tipo)
    if fecha_inicio:
        stmt = stmt.where(models.MovimientoInventario.fecha >= fecha_inicio)
    if fecha_fin:
        stmt = stmt.where(models.MovimientoInventario.fecha <= fecha_fin)

    # Orden server-side: los campos de nombre requieren su join (1:1, no multiplica filas).
    permitidos = {
        "fecha": models.MovimientoInventario.fecha,
        "hora": models.MovimientoInventario.hora,
        "elemento": models.ElementoInventario.nombre,
        "ubicacion": models.Ubicacion.nombre,
        "tipo": models.MovimientoInventario.tipo,
        "cantidad": models.MovimientoInventario.cantidad,
        "motivo": models.MovimientoInventario.motivo,
    }
    if orden == "elemento":
        stmt = stmt.join(
            models.ElementoInventario,
            models.MovimientoInventario.elemento_id == models.ElementoInventario.id,
        )
    if orden == "ubicacion":
        stmt = stmt.join(
            models.Ubicacion,
            models.MovimientoInventario.ubicacion_id == models.Ubicacion.id,
        )
    por_defecto = lambda s: s.order_by(
        models.MovimientoInventario.fecha.desc(),
        models.MovimientoInventario.hora.desc(),
        models.MovimientoInventario.id.desc(),
    )
    ordenar_fn = orden_validado(
        orden, dir_orden, permitidos, por_defecto, desempate=models.MovimientoInventario.id.desc()
    )

    ubs = _mapa_ubicaciones(db)
    usuarios = _mapa_usuarios(db)
    elementos = _mapa_elementos(db)
    total = int(db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one())
    filas = db.execute(
        ordenar_fn(stmt).limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()
    items = [
        MovimientoOut(
            id=m.id, elemento_id=m.elemento_id,
            elemento_nombre=elementos.get(m.elemento_id),
            ubicacion_id=m.ubicacion_id,
            ubicacion=ubs.get(m.ubicacion_id), tipo=m.tipo, cantidad=m.cantidad,
            responsable_id=m.responsable_id, responsable_nombre=usuarios.get(m.responsable_id),
            motivo=m.motivo, observaciones=m.observaciones,
            fecha=m.fecha, hora=m.hora,
        )
        for m in filas
    ]
    return como_pagina(items, total, page, page_size)


@router.get("/alertas", response_model=list[AlertaOut], summary="Existencias bajo mínimo")
def alertas(
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_LECTORES)),
):
    # El administrativo solo ve alertas de la Oficina.
    oficina = _oficina_id(db) if _solo_oficina(usuario) else None
    if _solo_oficina(usuario) and oficina is None:
        return []
    return svc_inventario.alertas(db, ubicacion_id=oficina)


# ----------------------------- Detalle de elemento ----------------------------
@router.get("/{elemento_id}", response_model=ElementoOut, summary="Obtener elemento")
def obtener_elemento(
    elemento_id: int,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_LECTORES)),
):
    elem = db.get(models.ElementoInventario, elemento_id)
    if not elem:
        raise HTTPException(404, "Elemento no encontrado")
    # El administrativo solo ve elementos con existencias en Oficina.
    if _solo_oficina(usuario):
        oficina = _oficina_id(db)
        if oficina is None:
            raise HTTPException(404, "Elemento no encontrado")
        tiene = db.execute(
            select(models.StockUbicacion).where(
                models.StockUbicacion.elemento_id == elemento_id,
                models.StockUbicacion.ubicacion_id == oficina,
            )
        ).scalars().first()
        if not tiene:
            raise HTTPException(404, "Elemento no encontrado")
        return _elemento_out(db, elem, ubicacion_id=oficina, solo_stock_ubicacion=oficina)
    return _elemento_out(db, elem)


@router.patch("/{elemento_id}", response_model=ElementoOut, summary="Actualizar elemento")
def actualizar_elemento(
    elemento_id: int,
    payload: ElementoUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    elem = db.get(models.ElementoInventario, elemento_id)
    if not elem:
        raise HTTPException(404, "Elemento no encontrado")
    datos = payload.model_dump(exclude_unset=True)
    for k, v in datos.items():
        setattr(elem, k, v)
    sellar(elem, usuario, nuevo=False)
    db.commit()
    db.refresh(elem)
    return _elemento_out(db, elem)


@router.delete("/{elemento_id}", summary="Eliminar elemento (soft delete)")
def eliminar_elemento(
    elemento_id: int,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    elem = db.get(models.ElementoInventario, elemento_id)
    if not elem:
        raise HTTPException(404, "Elemento no encontrado")
    elem.estado = models.EstadoRegistro.inactivo
    sellar(elem, usuario, nuevo=False)
    db.commit()
    return {"ok": True, "mensaje": "Elemento inactivado"}


# ----------------------------- Entradas / salidas -----------------------------
@router.post("/{elemento_id}/entrada", response_model=MovimientoOut, summary="Registrar entrada")
def entrada(
    elemento_id: int,
    payload: MovimientoCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_OPERARIO_QUIMICOS)),
):
    elem = db.get(models.ElementoInventario, elemento_id)
    if not elem:
        raise HTTPException(404, "Elemento no encontrado")
    # El operario solo ingresa químicos EN planta.
    _exigir_quimico_planta(db, usuario, elem.categoria_id, payload.ubicacion_id)
    fecha = payload.fecha or date.today()
    hora = payload.hora or datetime.now().time()
    svc_inventario.aplicar_movimiento(
        db, elem, payload.ubicacion_id, TipoMovimiento.entrada, float(payload.cantidad),
        usuario.id, payload.motivo, payload.observaciones, fecha, hora,
    )
    sellar(elem, usuario, nuevo=False)
    db.commit()
    mov = db.execute(
        select(models.MovimientoInventario)
        .where(models.MovimientoInventario.elemento_id == elemento_id)
        .order_by(models.MovimientoInventario.id.desc())
    ).scalars().first()
    ubs = _mapa_ubicaciones(db)
    return MovimientoOut(
        id=mov.id, elemento_id=mov.elemento_id, ubicacion_id=mov.ubicacion_id,
        ubicacion=ubs.get(mov.ubicacion_id), tipo=mov.tipo, cantidad=mov.cantidad,
        responsable_id=mov.responsable_id, motivo=mov.motivo, observaciones=mov.observaciones,
        fecha=mov.fecha, hora=mov.hora,
    )


@router.post("/{elemento_id}/salida", response_model=MovimientoOut, summary="Registrar salida")
def salida(
    elemento_id: int,
    payload: MovimientoCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(require_role(_ESCRITORES)),
):
    elem = db.get(models.ElementoInventario, elemento_id)
    if not elem:
        raise HTTPException(404, "Elemento no encontrado")
    fecha = payload.fecha or date.today()
    hora = payload.hora or datetime.now().time()
    svc_inventario.aplicar_movimiento(
        db, elem, payload.ubicacion_id, TipoMovimiento.salida, float(payload.cantidad),
        usuario.id, payload.motivo, payload.observaciones, fecha, hora,
    )
    sellar(elem, usuario, nuevo=False)
    db.commit()
    mov = db.execute(
        select(models.MovimientoInventario)
        .where(models.MovimientoInventario.elemento_id == elemento_id)
        .order_by(models.MovimientoInventario.id.desc())
    ).scalars().first()
    ubs = _mapa_ubicaciones(db)
    return MovimientoOut(
        id=mov.id, elemento_id=mov.elemento_id, ubicacion_id=mov.ubicacion_id,
        ubicacion=ubs.get(mov.ubicacion_id), tipo=mov.tipo, cantidad=mov.cantidad,
        responsable_id=mov.responsable_id, motivo=mov.motivo, observaciones=mov.observaciones,
        fecha=mov.fecha, hora=mov.hora,
    )
