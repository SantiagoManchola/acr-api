"""Servicio de exportación a CSV / XLSX / PDF (RNF-19)."""
import csv
import io
import os
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Iterable, Sequence

# Logos del membrete (tomados de la papelería oficial del acueducto).
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
LOGO_ACR = os.path.join(ASSETS_DIR, "logo_acr.png")
LOGO_SUPER = os.path.join(ASSETS_DIR, "logo_superservicios.png")

EMPRESA = "ACUEDUCTO COMUNITARIO BARRIO RICAURTE “ACUARICAURTE”"
EMPRESA_DETALLE = "J.A.C. - COMISIÓN EMPRESARIAL · NIT: 809000633-7 · CEL: 3227928798"
PIE_SLOGAN = "Acueducto Comunitario Acuaricaurte “Avanzando Juntos”"


def _a_texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, Enum):
        return str(v.value)
    if isinstance(v, bool):
        return "Sí" if v else "No"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        # Sin ceros decimales innecesarios (120.00 -> 120, 120.50 -> 120.5).
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if isinstance(v, (int, float, str)):
        return str(v)
    if hasattr(v, "value"):  # Enum de SQLAlchemy/Pydantic
        return str(v.value)
    return str(v)


def a_csv(filas: Sequence[dict], columnas: Sequence[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columnas))
    writer.writeheader()
    for f in filas:
        writer.writerow({c: _a_texto(f.get(c)) for c in columnas})
    return buf.getvalue()


def a_xlsx(filas: Sequence[dict], columnas: Sequence[str]) -> bytes:
    import pandas as pd

    datos = [{c: _a_texto(f.get(c)) for c in columnas} for f in filas]
    df = pd.DataFrame(datos, columns=list(columnas))
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="reporte")
    return buf.getvalue()


# Etiquetas legibles para las columnas de los reportes (PDF).
ETIQUETAS = {
    "tipo": "Tipo",
    "nombre": "Nombre",
    "codigo_usuario": "Código usuario",
    "codigo_facturacion": "Código facturación",
    "identificacion": "Identificación",
    "sector": "Sector",
    "tipo_usuario": "Tipo de usuario",
    "direccion": "Dirección",
    "serial": "Serial",
    "suscriptor": "Suscriptor",
    "fecha_instalacion": "Fecha instalación",
    "fecha": "Fecha",
    "hora": "Hora",
    "medidor": "Medidor",
    "lectura": "Lectura (m³)",
    "consumo": "Consumo (m³)",
    "promedio_usado": "Estimada",
    "irregular": "Irregular",
    "novedad": "Novedad",
    "foto_url": "Foto",
    "categoria": "Categoría",
    "ubicacion": "Ubicación",
    "cantidad": "Cantidad",
    "unidad": "Unidad",
    "minimo": "Mínimo",
    "valor": "Valor",
    "estado": "Estado",
    "parametro": "Parámetro",
    "fuera_rango": "Fuera de rango",
    "accion_correctiva": "Acción correctiva",
    "insumo": "Insumo",
    "tasa": "Tasa",
    "unidad_tasa": "Unidad de tasa",
    "observaciones": "Observaciones",
    "responsable": "Responsable",
    "horas": "Horas",
}


def _etiqueta(col: str) -> str:
    return ETIQUETAS.get(col, str(col).replace("_", " ").title())


def a_pdf(filas: Sequence[dict], columnas: Sequence[str], titulo: str) -> bytes:
    """PDF con membrete institucional (logos ACR + Superservicios), tabla y pie."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    AZUL = colors.HexColor("#2160AD")
    AZUL_CLARO = colors.HexColor("#EEF2FB")
    GRIS = colors.HexColor("#5B6B7B")
    BORDE = colors.HexColor("#D6E2F2")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(letter),
        title=titulo, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=36 * mm, bottomMargin=20 * mm,
    )

    generado = datetime.now().strftime("%d/%m/%Y %H:%M")

    def dibujar_membrete(canvas, documento):
        ancho, alto = landscape(letter)
        canvas.saveState()
        # Logos
        if os.path.exists(LOGO_ACR):
            canvas.drawImage(LOGO_ACR, 18 * mm, alto - 30 * mm, width=20 * mm, height=20 * mm,
                             preserveAspectRatio=True, mask="auto")
        if os.path.exists(LOGO_SUPER):
            canvas.drawImage(LOGO_SUPER, ancho - 18 * mm - 34 * mm, alto - 27 * mm,
                             width=34 * mm, height=12.4 * mm,
                             preserveAspectRatio=True, mask="auto")
        # Datos de la empresa (centro)
        canvas.setFillColor(colors.HexColor("#1B2733"))
        canvas.setFont("Helvetica-Bold", 9.5)
        canvas.drawCentredString(ancho / 2, alto - 17 * mm, EMPRESA)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRIS)
        canvas.drawCentredString(ancho / 2, alto - 22 * mm, EMPRESA_DETALLE)
        # Línea divisoria
        canvas.setStrokeColor(AZUL)
        canvas.setLineWidth(1.2)
        canvas.line(18 * mm, alto - 32 * mm, ancho - 18 * mm, alto - 32 * mm)
        # Pie
        canvas.setStrokeColor(BORDE)
        canvas.setLineWidth(0.6)
        canvas.line(18 * mm, 15 * mm, ancho - 18 * mm, 15 * mm)
        canvas.setFont("Helvetica-Oblique", 7.5)
        canvas.setFillColor(GRIS)
        canvas.drawString(18 * mm, 11 * mm, PIE_SLOGAN)
        canvas.drawRightString(ancho - 18 * mm, 11 * mm, f"Página {documento.page}")
        canvas.drawCentredString(ancho / 2, 11 * mm, f"Generado: {generado}")
        canvas.restoreState()

    estilo_titulo = ParagraphStyle(
        "titulo", fontName="Helvetica-Bold", fontSize=13, textColor=AZUL, spaceAfter=2,
    )
    estilo_sub = ParagraphStyle(
        "sub", fontName="Helvetica", fontSize=8.5, textColor=GRIS, spaceAfter=6,
    )
    estilo_celda = ParagraphStyle(
        "celda", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#1B2733"),
    )
    estilo_cab = ParagraphStyle(
        "cab", fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=colors.white,
    )

    elementos = [
        Paragraph(titulo, estilo_titulo),
        Paragraph(f"{len(filas)} registro(s) · generado el {generado}", estilo_sub),
        Spacer(1, 2),
    ]

    encabezado = [Paragraph(_etiqueta(c), estilo_cab) for c in columnas]
    cuerpo = [
        [Paragraph(_a_texto(f.get(c)) or "—", estilo_celda) for c in columnas]
        for f in filas
    ] or [[Paragraph("Sin datos para los filtros seleccionados.", estilo_celda)] + [""] * (len(columnas) - 1)]

    # Anchos proporcionales al contenido (con mínimo) para que quepan en la hoja.
    ancho_util = doc.width
    pesos = []
    for i, c in enumerate(columnas):
        largo = len(_etiqueta(c))
        for f in filas[:80]:
            largo = max(largo, len(_a_texto(f.get(c))))
        pesos.append(min(max(largo, 7), 44))
    total = sum(pesos)
    anchos = [max(16 * mm, ancho_util * p / total) for p in pesos]
    factor = ancho_util / sum(anchos)
    anchos = [w * factor for w in anchos]

    tabla = Table([encabezado] + cuerpo, colWidths=anchos, repeatRows=1)
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, AZUL_CLARO]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    elementos.append(tabla)

    doc.build(elementos, onFirstPage=dibujar_membrete, onLaterPages=dibujar_membrete)
    return buf.getvalue()

