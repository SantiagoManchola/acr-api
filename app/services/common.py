"""Helpers de auditoría (trazabilidad RNF-07), paginación y evidencias fotográficas."""

import math

from fastapi import HTTPException
from sqlalchemy import func, select

from ..config import settings


def sellar(obj, usuario, nuevo: bool = True) -> None:
    """Inyecta created_by/updated_by según el usuario autenticado."""
    if nuevo:
        obj.created_by = usuario.id
    obj.updated_by = usuario.id


def aplicar_paginacion(db, stmt, ordenar, page: int | None, page_size: int, many: bool = False):
    """Pagina un SELECT server-side (LIMIT/OFFSET + COUNT con los mismos filtros).

    `ordenar(stmt)` debe devolver el stmt con su ORDER_BY aplicado. Cuando
    `page` es None NO ejecuta el COUNT (compat: lista completa y total None).
    Con `many=True` devuelve filas completas (selects de varias columnas);
    por defecto devuelve entidades ORM (scalars).
    """
    def _ejecutar(q):
        res = db.execute(q)
        return res.all() if many else res.scalars().all()

    if page is None:
        return _ejecutar(ordenar(stmt)), None
    subq = stmt.order_by(None).subquery()
    total = int(db.execute(select(func.count()).select_from(subq)).scalar_one())
    filas = _ejecutar(ordenar(stmt).limit(page_size).offset((page - 1) * page_size))
    return filas, total


def como_pagina(items, total: int, page: int, page_size: int) -> dict:
    """Sobre paginado estándar: {items, total, page, page_size, pages}."""
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)) if page_size else 1,
    }


def orden_validado(orden: str | None, dir_orden: str | None, permitidos: dict, por_defecto, desempate=None):
    """ORDER BY server-side validado contra una lista blanca de campos.

    Con paginación, el orden DEBE resolverse en la API (ordenar solo la
    página cargada mezcla resultados entre páginas). `permitidos` mapea el
    nombre público del campo a su columna/expresión SQLAlchemy; se agrega un
    `desempate` (p. ej. el id) para que el orden sea determinista y las
    páginas no se mezclen cuando hay valores repetidos.
    """
    if orden and orden in permitidos:
        col = permitidos[orden]
        base = col.desc() if (dir_orden or "").lower() == "desc" else col.asc()
        extras = [base] + ([desempate] if desempate is not None else [])
        return lambda s: s.order_by(*extras)
    return por_defecto


def condiciones_busqueda(campo, texto: str | None):
    """Condiciones AND: cada palabra del texto debe aparecer en el campo.

    Hace la búsqueda permisiva: «simon acosta», «acosta simon» o
    «martinez acosta» encuentran «ACOSTA MARTINEZ SIMON». La colación
    utf8mb4_unicode_ci de MySQL ignora mayúsculas y tildes.
    """
    if not texto:
        return []
    return [campo.ilike(f"%{t}%") for t in str(texto).split() if t]


def validar_foto_url(foto_url: str | None) -> str | None:
    """Valida la URL de una evidencia fotográfica (opcional en los 3 puntos).

    - None/vacío: permitido siempre (la foto es opcional).
    - Con R2 configurado: debe empezar por la base pública (garantiza que la
      foto pasó por el flujo firmado /evidencias/presign).
    - Sin R2 configurado: se rechaza cualquier URL (501 claro en vez de
      guardar enlaces que no existen).
    """
    if not foto_url:
        return None
    if not settings.r2_enabled:
        raise HTTPException(
            501,
            "Evidencias fotográficas no configuradas: faltan las variables R2_* en el .env del API.",
        )
    base = settings.r2_public_base_url.rstrip("/") + "/"
    if not foto_url.startswith(base):
        raise HTTPException(400, "foto_url inválida: debe ser la URL pública devuelta por /evidencias/presign")
    return foto_url
