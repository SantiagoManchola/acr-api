"""Preview y generación en memoria del formato mensual de facturación."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..security import get_db, require_role
from ..services.facturacion import (
    MAX_ARCHIVO_BYTES,
    PlantillaFacturacionError,
    analizar_facturacion,
    generar_archivo_facturacion,
)

router = APIRouter(prefix="/facturacion", tags=["Facturación"])
_ROLES_FACTURACION = ["admin", "administrativo"]
_TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _leer_archivo(archivo: UploadFile) -> bytes:
    nombre = (archivo.filename or "").lower()
    if not nombre.endswith(".xlsx"):
        raise HTTPException(400, "Sube el archivo del formato contable en Excel .xlsx.")
    contenido = archivo.file.read(MAX_ARCHIVO_BYTES + 1)
    if len(contenido) > MAX_ARCHIVO_BYTES:
        raise HTTPException(413, "El archivo supera el máximo permitido de 20 MB.")
    if not contenido:
        raise HTTPException(400, "El archivo está vacío.")
    return contenido


def _analizar(archivo: UploadFile, mes: int, anio: int, fecha_desde: date,
              fecha_hasta: date, db: Session):
    contenido = _leer_archivo(archivo)
    try:
        return contenido, analizar_facturacion(
            db, contenido, mes, anio, fecha_desde, fecha_hasta
        )
    except PlantillaFacturacionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/previsualizar", summary="Validar plantilla y previsualizar valores de facturación")
def previsualizar_facturacion(
    archivo: UploadFile = File(...),
    mes: int = Form(..., ge=1, le=12),
    anio: int = Form(..., ge=2000, le=2100),
    fecha_desde: date = Form(...),
    fecha_hasta: date = Form(...),
    db: Session = Depends(get_db),
    _: object = Depends(require_role(_ROLES_FACTURACION)),
):
    """No persiste el archivo ni modifica lecturas; devuelve sugerencias y novedades."""
    _, resultado = _analizar(archivo, mes, anio, fecha_desde, fecha_hasta, db)
    return {
        "resumen": resultado["resumen"],
        "novedades": resultado["novedades"],
        "auditoria": resultado["auditoria"],
    }


@router.post("/generar", summary="Descargar una copia del Excel completada para facturación")
def generar_facturacion(
    archivo: UploadFile = File(...),
    mes: int = Form(..., ge=1, le=12),
    anio: int = Form(..., ge=2000, le=2100),
    fecha_desde: date = Form(...),
    fecha_hasta: date = Form(...),
    confirmar_reemplazo: bool = Form(default=False),
    db: Session = Depends(get_db),
    _: object = Depends(require_role(_ROLES_FACTURACION)),
):
    """Genera una copia nueva; la plantilla subida y la DB permanecen intactas."""
    contenido, resultado = _analizar(archivo, mes, anio, fecha_desde, fecha_hasta, db)
    ocupadas = resultado["resumen"]["celdas_mes_con_datos"]
    if ocupadas and not confirmar_reemplazo:
        raise HTTPException(
            409,
            f"La columna {resultado['resumen']['mes_nombre']} ya tiene {ocupadas} celdas con datos. "
            "Confirma reemplazarlas en la copia descargada.",
        )
    try:
        xlsx = generar_archivo_facturacion(
            contenido,
            resultado["resumen"]["hoja"],
            resultado["planes"],
        )
    except PlantillaFacturacionError as exc:
        raise HTTPException(400, str(exc)) from exc

    nombre = f"Facturacion_{resultado['resumen']['mes']}_{anio}.xlsx"
    return Response(
        content=xlsx,
        media_type=_TIPO_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
