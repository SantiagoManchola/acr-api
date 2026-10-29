"""Lógica de planta: valores fuera de rango (RF-48/RF-49) y estado operativo
del diagrama por partes (RF-55/RF-56)."""
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from .common import ahora_colombia, aplicar_paginacion, hoy_colombia, orden_validado, sellar


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

    Con cantidad 0 (cambio de tasa/medición sin incorporar químico) no toca el
    stock ni genera movimientos de inventario: devuelve [].
    """
    c = Decimal(str(cantidad))
    if c <= 0:
        return []
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


# ----------------------------- Estado operativo de la planta -----------------
def _ultimo_estado_subquery():
    """Id del último evento de cada componente (el estado actual)."""
    return (
        select(
            models.EstadoComponentePlanta.componente_id.label("componente_id"),
            func.max(models.EstadoComponentePlanta.id).label("ultimo_id"),
        )
        .group_by(models.EstadoComponentePlanta.componente_id)
        .subquery()
    )


def _stmt_componentes(incluir_inactivos: bool = False):
    ultimo = _ultimo_estado_subquery()
    stmt = (
        select(models.ComponentePlanta, models.EstadoComponentePlanta, models.Usuario.nombre)
        .outerjoin(ultimo, ultimo.c.componente_id == models.ComponentePlanta.id)
        .outerjoin(
            models.EstadoComponentePlanta,
            models.EstadoComponentePlanta.id == ultimo.c.ultimo_id,
        )
        .outerjoin(
            models.Usuario,
            models.EstadoComponentePlanta.responsable_id == models.Usuario.id,
        )
    )
    if not incluir_inactivos:
        stmt = stmt.where(models.ComponentePlanta.estado == models.EstadoRegistro.activo)
    return stmt.order_by(models.ComponentePlanta.orden, models.ComponentePlanta.nombre)


def _componente_dict(componente, estado, responsable_nombre):
    if estado is None:
        # Compatibilidad con componentes creados antes de existir el historial:
        # se asume funcionando, tal como se definió para el arranque.
        fecha = componente.created_at.date() if componente.created_at else None
        hora = componente.created_at.time().replace(microsecond=0) if componente.created_at else None
        estado_actual = models.EstadoOperativoComponente.funcionando
        nota_actual = "Estado inicial"
        responsable_id = None
        responsable_nombre = None
    else:
        fecha = estado.fecha
        hora = estado.hora
        estado_actual = estado.estado
        nota_actual = estado.nota
        responsable_id = estado.responsable_id
    return {
        "id": componente.id,
        "nombre": componente.nombre,
        "descripcion": componente.descripcion,
        "orden": componente.orden,
        "estado": componente.estado,
        "estado_actual": estado_actual,
        "nota_actual": nota_actual,
        "fecha_actualizacion": fecha,
        "hora_actualizacion": hora,
        "responsable_id": responsable_id,
        "responsable_nombre": responsable_nombre,
    }


def componentes_con_estado_actual(db: Session, incluir_inactivos: bool = False):
    filas = db.execute(_stmt_componentes(incluir_inactivos)).all()
    return [_componente_dict(c, e, resp) for (c, e, resp) in filas]


def componente_con_estado_actual(db: Session, componente_id: int, incluir_inactivo: bool = True):
    stmt = _stmt_componentes(incluir_inactivo).where(models.ComponentePlanta.id == componente_id)
    fila = db.execute(stmt).first()
    if not fila:
        return None
    componente, estado, responsable = fila
    return _componente_dict(componente, estado, responsable)


def _texto_limpio(valor: str | None) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _validar_nombre_componente(db: Session, nombre: str, excluir_id: int | None = None) -> str:
    nombre = _texto_limpio(nombre)
    if not nombre:
        raise HTTPException(400, "El nombre de la parte es obligatorio")
    stmt = select(models.ComponentePlanta).where(
        func.lower(models.ComponentePlanta.nombre) == nombre.lower()
    )
    if excluir_id is not None:
        stmt = stmt.where(models.ComponentePlanta.id != excluir_id)
    if db.execute(stmt).scalars().first():
        raise HTTPException(400, "Ya existe una parte de la planta con ese nombre")
    return nombre


def _ultimo_estado(db: Session, componente_id: int):
    return db.execute(
        select(models.EstadoComponentePlanta)
        .where(models.EstadoComponentePlanta.componente_id == componente_id)
        .order_by(models.EstadoComponentePlanta.id.desc())
        .limit(1)
    ).scalars().first()


def _asegurar_estado_inicial(db: Session, componente, usuario):
    """Crea el evento inicial 'funcionando' si la parte aún no tiene historial."""
    if componente.estado != models.EstadoRegistro.activo:
        return
    if _ultimo_estado(db, componente.id) is not None:
        return
    evento = models.EstadoComponentePlanta(
        componente_id=componente.id,
        estado=models.EstadoOperativoComponente.funcionando,
        nota="Estado inicial",
        fecha=hoy_colombia(),
        hora=ahora_colombia().time().replace(microsecond=0),
        responsable_id=usuario.id,
    )
    sellar(evento, usuario, nuevo=True)
    db.add(evento)


def crear_componente(db: Session, payload, usuario):
    nombre = _validar_nombre_componente(db, payload.nombre)
    orden = payload.orden
    if orden is None:
        max_orden = db.execute(select(func.max(models.ComponentePlanta.orden))).scalar()
        orden = int(max_orden or 0) + 1
    componente = models.ComponentePlanta(
        nombre=nombre,
        descripcion=_texto_limpio(payload.descripcion),
        orden=orden,
        estado=models.EstadoRegistro.activo,
    )
    sellar(componente, usuario, nuevo=True)
    db.add(componente)
    db.flush()
    _asegurar_estado_inicial(db, componente, usuario)
    db.commit()
    db.refresh(componente)
    return componente


def actualizar_componente(db: Session, componente, payload, usuario):
    datos = payload.model_dump(exclude_unset=True)
    if "nombre" in datos:
        if datos["nombre"] is None:
            raise HTTPException(400, "El nombre de la parte es obligatorio")
        datos["nombre"] = _validar_nombre_componente(db, datos["nombre"], excluir_id=componente.id)
    if "descripcion" in datos:
        datos["descripcion"] = _texto_limpio(datos["descripcion"])
    for campo, valor in datos.items():
        if campo == "orden" and valor is None:
            continue
        if campo == "estado" and valor is None:
            continue
        setattr(componente, campo, valor)
    sellar(componente, usuario, nuevo=False)
    if componente.estado == models.EstadoRegistro.activo:
        _asegurar_estado_inicial(db, componente, usuario)
    db.commit()
    db.refresh(componente)
    return componente


def reordenar_componentes(db: Session, ids: list[int], usuario):
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "La lista de partes contiene ids repetidos")
    componentes = db.execute(
        select(models.ComponentePlanta).where(models.ComponentePlanta.id.in_(ids))
    ).scalars().all()
    if len(componentes) != len(ids):
        raise HTTPException(400, "Una o más partes no existen")
    por_id = {c.id: c for c in componentes}
    for posicion, componente_id in enumerate(ids, start=1):
        componente = por_id[componente_id]
        componente.orden = posicion
        sellar(componente, usuario, nuevo=False)
    db.commit()
    return componentes_con_estado_actual(db, incluir_inactivos=True)


def registrar_estado_componente(db: Session, componente, payload, usuario):
    if componente.estado != models.EstadoRegistro.activo:
        raise HTTPException(400, "La parte está inactiva; actívela antes de cambiar su estado")
    nota = _texto_limpio(payload.nota)
    if payload.estado != models.EstadoOperativoComponente.funcionando and not nota:
        raise HTTPException(
            400,
            "La nota es obligatoria cuando el estado es Afectación o Fuera de funcionamiento",
        )
    ultimo = _ultimo_estado(db, componente.id)
    if ultimo is not None and ultimo.estado == payload.estado and not nota:
        raise HTTPException(400, "Seleccione un estado diferente o agregue una nota")

    evento = models.EstadoComponentePlanta(
        componente_id=componente.id,
        estado=payload.estado,
        nota=nota,
        fecha=hoy_colombia(),
        hora=ahora_colombia().time().replace(microsecond=0),
        responsable_id=usuario.id,
    )
    sellar(evento, usuario, nuevo=True)
    db.add(evento)
    db.commit()
    db.refresh(evento)
    return {
        "id": evento.id,
        "componente_id": componente.id,
        "componente_nombre": componente.nombre,
        "estado": evento.estado,
        "nota": evento.nota,
        "fecha": evento.fecha,
        "hora": evento.hora,
        "responsable_id": evento.responsable_id,
        "responsable_nombre": usuario.nombre,
        "created_at": evento.created_at,
    }


def filtrar_estados_componente(db: Session, *, componente_id=None, estado=None,
                               fecha_inicio=None, fecha_fin=None,
                               page: int | None = None, page_size: int = 20,
                               orden: str | None = None, dir_orden: str | None = None):
    stmt = (
        select(
            models.EstadoComponentePlanta,
            models.ComponentePlanta.nombre,
            models.Usuario.nombre,
        )
        .join(
            models.ComponentePlanta,
            models.EstadoComponentePlanta.componente_id == models.ComponentePlanta.id,
        )
        .outerjoin(
            models.Usuario,
            models.EstadoComponentePlanta.responsable_id == models.Usuario.id,
        )
    )
    if componente_id:
        stmt = stmt.where(models.EstadoComponentePlanta.componente_id == componente_id)
    if estado is not None:
        stmt = stmt.where(models.EstadoComponentePlanta.estado == estado)
    stmt = _rango_fechas(stmt, models.EstadoComponentePlanta, fecha_inicio, fecha_fin)

    permitidos = {
        "fecha": models.EstadoComponentePlanta.fecha,
        "hora": models.EstadoComponentePlanta.hora,
        "componente": models.ComponentePlanta.nombre,
        "estado": models.EstadoComponentePlanta.estado,
        "responsable": models.Usuario.nombre,
        "nota": models.EstadoComponentePlanta.nota,
    }
    por_defecto = lambda s: s.order_by(
        models.EstadoComponentePlanta.fecha.desc(),
        models.EstadoComponentePlanta.hora.desc(),
        models.EstadoComponentePlanta.id.desc(),
    )
    ordenar = orden_validado(
        orden, dir_orden, permitidos, por_defecto,
        desempate=models.EstadoComponentePlanta.id.desc(),
    )
    filas, total = aplicar_paginacion(db, stmt, ordenar, page, page_size, many=True)
    items = [
        {
            **{c.name: getattr(e, c.name) for c in models.EstadoComponentePlanta.__table__.columns},
            "componente_nombre": componente_nombre,
            "responsable_nombre": responsable_nombre,
        }
        for (e, componente_nombre, responsable_nombre) in filas
    ]
    return items, total
