"""Evidencias fotográficas en Cloudflare R2 (S3-compatible).

Flujo (foto OPCIONAL, bucket público de lectura):
  1. Frontend comprime la imagen en el navegador (WebP, máx 1600px) para
     no ocupar espacio de más en R2 sin perder calidad visible.
  2. Frontend pide `POST /evidencias/presign` -> recibe URL firmada PUT.
  3. Frontend sube el archivo DIRECTO a R2 con PUT (el API no ve los bytes).
  4. Frontend confirma con `POST /evidencias/confirmar` (verifica que el
     objeto exista y no exceda el tamaño máximo; si lo excede se borra).
  5. Frontend crea el registro (medición/lectura/actividad) con `foto_url`.

NOTA: R2 NO soporta subidas POST (presigned POST / HTML forms): responde
`501 Not Implemented`. Por eso se usa URL firmada PUT, que sí está soportada.
Ver https://developers.cloudflare.com/r2/api/s3/presigned-urls/

Seguridad: URL firmada por objeto (clave aleatoria UUID), Content-Type
firmado (el cliente debe enviar el mismo header), expiración corta y
verificación posterior de tamaño con borrado si excede el límite.
Solo roles escritores de cada módulo pueden pedir firmas.
"""
import uuid
from datetime import date

from ..config import settings

# Tipos aceptados (el frontend siempre envía WebP o JPEG tras comprimir).
TIPOS_PERMITIDOS = {
    "image/webp": "webp",
    "image/jpeg": "jpg",
    "image/png": "png",
}
# Límite por archivo ya comprimido: 8 MB da margen de sobra
# (una foto 1600px WebP q82 pesa típicamente 150-500 KB).
TAMANO_MAXIMO_BYTES = 8 * 1024 * 1024
EXPIRACION_SEGUNDOS = 900  # 15 min para completar la subida
MODULOS = ("medicion", "lectura", "actividad")


def _cliente():
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError(
            "Falta la dependencia 'boto3': instálala con `pip install -r requirements.txt`."
        ) from e
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def _validar_modulo_tipo(modulo: str, content_type: str) -> str:
    if modulo not in MODULOS:
        raise ValueError(f"modulo debe ser uno de {MODULOS}")
    if content_type not in TIPOS_PERMITIDOS:
        raise ValueError(f"content_type debe ser uno de {sorted(TIPOS_PERMITIDOS)}")
    return TIPOS_PERMITIDOS[content_type]


def _nueva_key(modulo: str, ext: str) -> str:
    hoy = date.today()
    return f"evidencias/{modulo}/{hoy:%Y}/{hoy:%m}/{uuid.uuid4().hex}.{ext}"


def _validar_key(key: str) -> str:
    """La clave debe vivir bajo evidencias/{modulo}/ y sin saltos de ruta."""
    if not key or ".." in key or "\\" in key:
        raise ValueError("key inválida")
    partes = key.split("/")
    if len(partes) != 5 or partes[0] != "evidencias" or partes[1] not in MODULOS:
        raise ValueError("key inválida: debe ser la devuelta por /evidencias/presign")
    return key


def generar_presign(modulo: str, content_type: str) -> dict:
    """Genera una URL firmada PUT para subir una evidencia a R2.

    El cliente debe hacer `PUT url` con el header `Content-Type`
    exactamente igual (va firmado en la URL).
    """
    ext = _validar_modulo_tipo(modulo, content_type)
    key = _nueva_key(modulo, ext)
    cliente = _cliente()
    url = cliente.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.r2_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=EXPIRACION_SEGUNDOS,
    )
    base = settings.r2_public_base_url.rstrip("/")
    return {
        "url": url,
        "key": key,
        "content_type": content_type,
        "public_url": f"{base}/{key}",
        "expires_in": EXPIRACION_SEGUNDOS,
        "tamano_maximo_bytes": TAMANO_MAXIMO_BYTES,
    }


def confirmar_objeto(key: str) -> dict:
    """Verifica con HEAD que el objeto exista y cumpla el límite de tamaño.

    Si excede el límite se borra y se informa para reintentar con una
    imagen más liviana. Devuelve {key, tamano_bytes, content_type}.
    """
    key = _validar_key(key)
    cliente = _cliente()
    try:
        head = cliente.head_object(Bucket=settings.r2_bucket, Key=key)
    except Exception as e:
        raise ValueError(
            "La foto aún no está en el almacenamiento: complete la subida e intente de nuevo."
        ) from e
    tamano = int(head.get("ContentLength") or 0)
    if tamano < 1 or tamano > TAMANO_MAXIMO_BYTES:
        try:
            cliente.delete_object(Bucket=settings.r2_bucket, Key=key)
        finally:
            pass
        raise ValueError(
            f"La foto excede el tamaño máximo ({TAMANO_MAXIMO_BYTES // (1024 * 1024)} MB): "
            "se eliminó, comprima la imagen e intente de nuevo."
        )
    return {
        "key": key,
        "tamano_bytes": tamano,
        "content_type": head.get("ContentType") or "",
    }
