"""Helpers de auditoría (trazabilidad RNF-07) y evidencias fotográficas."""

from fastapi import HTTPException

from ..config import settings


def sellar(obj, usuario, nuevo: bool = True) -> None:
    """Inyecta created_by/updated_by según el usuario autenticado."""
    if nuevo:
        obj.created_by = usuario.id
    obj.updated_by = usuario.id


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
