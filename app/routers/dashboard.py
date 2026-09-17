"""Router del dashboard: resumen por rol en UNA petición (agregados SQL).

El CMS ya no consulta listas completas para pintar el dashboard: cada
sección se calcula en la API y el payload se recorta según el rol.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models
from ..schemas import DashboardResumen
from ..security import get_current_user, get_db
from ..services import dashboard as svc
from ..services import planta as svc_planta

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

# Mismos permisos que el CMS aplica por rol en las vistas.
_ROLES_INVENTARIO = ["admin", "administrativo", "operario"]
_ROLES_MM = ["admin", "administrativo", "operario", "fontanero"]
_ROLES_PLANTA = ["admin", "operario"]


@router.get("/resumen", response_model=DashboardResumen, summary="Resumen del dashboard por rol")
def resumen(
    dias_consumo: int = Query(default=60, ge=1, le=365),
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(get_current_user),
):
    rol = usuario.rol.nombre if usuario.rol else ""
    puede_inventario = rol in _ROLES_INVENTARIO
    puede_mm = rol in _ROLES_MM
    puede_planta = rol in _ROLES_PLANTA
    ver_quimicos = puede_inventario and rol != "administrativo"
    ver_consumo = puede_mm and rol not in ("fontanero", "operario")
    ver_medidores = puede_mm and rol != "operario"

    # El administrativo solo tiene alcance de la ubicación Oficina.
    oficina_id = None
    if puede_inventario and rol == "administrativo":
        oficina_id = svc.ubicacion_id(db, "oficina")

    return {
        "rol": rol,
        "inventario": svc.resumen_inventario(db, oficina_id=oficina_id) if puede_inventario else None,
        "quimicos_planta": svc.quimicos_planta(db, limite=6) if ver_quimicos else None,
        "micromedidores": (
            svc.resumen_micromedicion(db, dias=dias_consumo, ver_medidores=ver_medidores, ver_consumo=ver_consumo)
            if puede_mm else None
        ),
        "planta": {
            "fuera_rango": svc_planta.parametros_fuera_rango(db),
        } if puede_planta else None,
    }
