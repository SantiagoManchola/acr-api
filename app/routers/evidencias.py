"""Router de evidencias fotográficas (firma de subidas directas a R2)."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .. import models
from ..config import settings
from ..security import require_role
from ..services import storage as svc_storage

router = APIRouter(prefix="/evidencias", tags=["Evidencias"])

# Quién puede pedir firmas por módulo (mismos escritores de cada registro):
# - medicion/actividad (planta): admin, operario
# - lectura (micromedidores): admin, administrativo, fontanero
_ROLES_MODULO = {
    "medicion": ["admin", "operario"],
    "actividad": ["admin", "operario"],
    "lectura": ["admin", "administrativo", "fontanero"],
}
_TODOS_ESCRITORES = sorted({r for roles in _ROLES_MODULO.values() for r in roles})


class PresignRequest(BaseModel):
    modulo: str = Field(description="medicion | lectura | actividad")
    content_type: str = Field(description="image/webp | image/jpeg | image/png")


class ConfirmarRequest(BaseModel):
    key: str = Field(description="Clave devuelta por /evidencias/presign")


def _exigir_r2():
    if not settings.r2_enabled:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "Evidencias fotográficas no configuradas: faltan las variables R2_* en el .env del API.",
        )


@router.post("/presign", summary="Firmar subida directa (PUT) de una evidencia a R2")
def presign(
    payload: PresignRequest,
    usuario: models.Usuario = Depends(require_role(_TODOS_ESCRITORES)),
):
    _exigir_r2()
    permitidos = _ROLES_MODULO.get(payload.modulo)
    if permitidos is None:
        raise HTTPException(400, "modulo debe ser medicion, lectura o actividad")
    rol = usuario.rol.nombre if usuario.rol else None
    if rol not in permitidos:
        raise HTTPException(403, f"El rol '{rol}' no puede subir evidencias de {payload.modulo}")
    try:
        return svc_storage.generar_presign(payload.modulo, payload.content_type)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(e))


@router.post("/confirmar", summary="Confirmar evidencia subida a R2 (verifica tamaño)")
def confirmar(
    payload: ConfirmarRequest,
    _: models.Usuario = Depends(require_role(_TODOS_ESCRITORES)),
):
    _exigir_r2()
    try:
        return svc_storage.confirmar_objeto(payload.key)
    except ValueError as e:
        code = 413 if "excede el tamaño" in str(e) else 400
        raise HTTPException(code, str(e))
    except RuntimeError as e:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(e))
