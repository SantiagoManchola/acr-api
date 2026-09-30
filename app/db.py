"""Conexión a MySQL y sesiones de SQLAlchemy 2.0.

Zona horaria: el servidor del API y MySQL operan en UTC (UTC-0), pero el
SISTEMA trabaja y muestra hora de COLOMBIA (UTC-5):

- Cada conexión nueva fija la zona horaria de su SESIÓN MySQL en UTC-5
  (event listener «connect»), de modo que `func.now()` —server_default y
  onupdate de created_at/updated_at, y el último_acceso del login— escribe
  hora colombiana sin cambiar la configuración global del servidor.
- Las fechas/horas por defecto del código usan los helpers
  hoy_colombia() / ahora_colombia() de services/common.py.

Nota: los JWT (security.py) usan UTC a propósito: es el estándar para `exp`.
"""
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings
from .services.common import ZONA_COLOMBIA


def tz_sql_colombia() -> str:
    """Offset de Colombia en formato MySQL a partir de la zona definida en
    services/common (p. ej. '-05:00'). Colombia es fija: sin horario de verano."""
    off = datetime.now(ZONA_COLOMBIA).utcoffset()
    minutos = int(off.total_seconds()) // 60 if off else -300
    signo = "+" if minutos >= 0 else "-"
    horas, mins = divmod(abs(minutos), 60)
    return f"{signo}{horas:02d}:{mins:02d}"


engine = create_engine(settings.database_url, pool_pre_ping=True)


@event.listens_for(engine, "connect")
def _fijar_hora_colombia(dbapi_conexion, _registro):
    """Fija la zona horaria de la SESIÓN MySQL en Colombia (UTC-5).

    Se ejecuta por cada conexión nueva del pool. Así NOW()/CURRENT_TIMESTAMP
    (que MySQL resuelve con la zona de sesión) devuelve hora colombiana
    aunque el servidor global esté en UTC.
    """
    if engine.dialect.name in ("mysql", "mariadb"):
        cursor = dbapi_conexion.cursor()
        try:
            cursor.execute(f"SET time_zone = '{tz_sql_colombia()}'")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass
