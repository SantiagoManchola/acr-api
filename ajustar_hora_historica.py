"""Ajuste ÚNICO de histórico: el sistema guardaba hora UTC (servidor en UTC-0).

Desde el cambio de zona horaria, la API escribe y muestra hora de COLOMBIA
(UTC-5). Este script ATRASA 5 HORAS las fechas/horas de auditoría ya
guardadas en UTC para que queden en hora colombiana:

- `created_at` y `updated_at` de TODAS las tablas
- `ultimo_acceso` de la tabla usuarios

Las fechas «de negocio» (fecha de una lectura, de una medición, etc.) NO se
tocan: representan el día en que se tomó el dato en campo.

Ejecútalo UNA SOLA VEZ, desde la carpeta `api/`, idealmente con el sistema
sin uso (detén la API, corre el script y vuelve a iniciarla):

    python ajustar_hora_historica.py              # simulación (no modifica)
    python ajustar_hora_historica.py --ejecutar   # aplica el cambio
"""
import sys

from sqlalchemy import text

from app import models
from app.db import engine

# Colombia es UTC-5 fijo (sin horario de verano): UTC -> Colombia = -5 horas.
HORAS = 5
# Nota: este ajuste YA FUE APLICADO a la base de producción (Aiven) el
# 2026-09-30, incluyendo la corrección de hora de negocio de lecturas,
# mediciones y movimientos generadas en vivo. NO volver a ejecutarlo.

# Columnas de auditoría presentes en todas las tablas + la de último acceso.
COLUMNAS_AUDITORIA = ("created_at", "updated_at")
ULTIMO_ACCESO = "ultimo_acceso"


def _objetivos():
    """(tabla, columnas) con columnas de auditoría según los modelos."""
    for tabla in models.Base.metadata.sorted_tables:
        cols = [c.name for c in tabla.columns if c.name in COLUMNAS_AUDITORIA]
        if tabla.name == "usuarios" and any(c.name == ULTIMO_ACCESO for c in tabla.columns):
            cols.append(ULTIMO_ACCESO)
        if cols:
            yield tabla.name, cols


def main() -> None:
    ejecutar = "--ejecutar" in sys.argv
    print(f"Ajuste de histórico UTC -> Colombia (+{HORAS} horas)")
    print("Modo:", "EJECUTAR (modifica datos)" if ejecutar else "SIMULACIÓN (no modifica)")
    print()

    total_filas = 0
    with engine.connect() as conn:
        for tabla, cols in _objetivos():
            for col in cols:
                conteo = conn.execute(
                    text(f"SELECT COUNT(*) FROM `{tabla}` WHERE `{col}` IS NOT NULL")
                ).scalar()
                if not conteo:
                    continue
                print(f"  {tabla}.{col}: {conteo} fila(s)")
                total_filas += conteo
                if ejecutar:
                    conn.execute(
                        text(
                            f"UPDATE `{tabla}` SET `{col}` = `{col}` - INTERVAL {HORAS} HOUR"
                        )
                    )
        if ejecutar:
            conn.commit()

    print()
    if ejecutar:
        print(f"Listo: {total_filas} valor(es) ajustados a hora de Colombia.")
    else:
        print(f"Simulación: se ajustarían {total_filas} valor(es).")
        print("Ejecuta con --ejecutar para aplicar el cambio.")


if __name__ == "__main__":
    main()
