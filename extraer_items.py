#!/usr/bin/env python3
"""
Módulo 2 — Extractor de Ítems desde PDFs de Pliegos
Marc Chagall Laboratorio Fotográfico — Salta, Argentina

Lee pliegos PDF descargados y extrae la tabla de ítems:
descripción, cantidad, unidad de medida.
"""

import json
import logging
import re
from pathlib import Path

import pdfplumber

# ── Configuración de logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/extraer_items.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
LICITACIONES_FILE = BASE_DIR / "licitaciones.json"
(BASE_DIR / "logs").mkdir(exist_ok=True)

# ── Persistencia ──────────────────────────────────────────────────────────────

def load_licitaciones() -> list[dict]:
    if not LICITACIONES_FILE.exists():
        log.error("No existe licitaciones.json. Ejecutá primero scraper.py")
        return []
    with open(LICITACIONES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_licitaciones(data: list[dict]) -> None:
    with open(LICITACIONES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Normalización de texto ────────────────────────────────────────────────────

def limpiar(texto: str) -> str:
    if not texto:
        return ""
    return re.sub(r"\s+", " ", texto).strip()


def parsear_numero(texto: str) -> float | None:
    """Convierte '1.200,50' o '1200.50' a float."""
    if not texto:
        return None
    texto = texto.strip()
    # Formato argentino: punto de miles, coma decimal
    if re.match(r"^[\d.]+,\d+$", texto):
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", "")
    try:
        return float(texto)
    except ValueError:
        return None


# ── Detección de columnas ─────────────────────────────────────────────────────

# Patrones para identificar columnas clave en encabezados de tabla
_COL_PATTERNS = {
    "numero":      re.compile(r"^n[°º]?\.?$|^item$|^#$|^nro\.?$", re.I),
    "descripcion": re.compile(r"descrip|denominaci|detalle|artículo|producto|especif", re.I),
    "cantidad":    re.compile(r"cant(?:idad)?\.?|^qty$|^q\.?$", re.I),
    "unidad":      re.compile(r"unidad|u\.m\.|um|medida|ud\.?", re.I),
    "precio":      re.compile(r"precio|costo|valor|unitario", re.I),
}


def detectar_columnas(header_row: list[str]) -> dict[str, int]:
    """Mapea nombres semánticos a índices de columna."""
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        texto = limpiar(str(cell or ""))
        for nombre, pat in _COL_PATTERNS.items():
            if nombre not in mapping and pat.search(texto):
                mapping[nombre] = idx
    return mapping


# ── Extracción desde tabla pdfplumber ────────────────────────────────────────

def extraer_items_tabla(pdf_path: Path) -> list[dict]:
    """Extrae ítems usando pdfplumber table extraction."""
    items = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for num_pagina, pagina in enumerate(pdf.pages, 1):
                tablas = pagina.extract_tables()
                for tabla in tablas:
                    if not tabla or len(tabla) < 2:
                        continue

                    # Buscar fila de encabezado
                    header_idx = 0
                    col_map = {}
                    for i, fila in enumerate(tabla[:5]):  # encabezado en primeras 5 filas
                        col_map = detectar_columnas([str(c or "") for c in fila])
                        if "descripcion" in col_map:
                            header_idx = i
                            break

                    if "descripcion" not in col_map:
                        continue

                    log.info(
                        "  Tabla con columnas mapeadas en pág %d: %s",
                        num_pagina, col_map
                    )

                    for fila in tabla[header_idx + 1:]:
                        if not fila:
                            continue
                        try:
                            desc_idx = col_map.get("descripcion", 1)
                            cant_idx = col_map.get("cantidad")
                            unid_idx = col_map.get("unidad")
                            num_idx  = col_map.get("numero", 0)

                            descripcion = limpiar(str(fila[desc_idx] or ""))
                            if not descripcion or len(descripcion) < 3:
                                continue

                            # Ignorar filas de subtotales / totales
                            if re.match(r"^(total|subtotal|iva|suma)", descripcion, re.I):
                                continue

                            cantidad = None
                            if cant_idx is not None and cant_idx < len(fila):
                                cantidad = parsear_numero(str(fila[cant_idx] or ""))

                            unidad = ""
                            if unid_idx is not None and unid_idx < len(fila):
                                unidad = limpiar(str(fila[unid_idx] or ""))

                            numero = ""
                            if num_idx < len(fila):
                                numero = limpiar(str(fila[num_idx] or ""))

                            items.append({
                                "numero": numero,
                                "descripcion": descripcion,
                                "cantidad": cantidad,
                                "unidad": unidad,
                                "precio_referencia": None,
                                "fuente_precio": None,
                            })
                        except (IndexError, Exception) as e:
                            log.debug("Error en fila: %s", e)
    except Exception as e:
        log.error("Error leyendo PDF con pdfplumber: %s", e)

    return items


# ── Extracción desde texto libre ──────────────────────────────────────────────

# Patrones para detectar ítems en texto libre
_ITEM_PATTERNS = [
    # "1. Papel fotográfico A4 mate   500   resmas"
    re.compile(
        r"^\s*(\d+)[.)]\s+(.+?)\s{2,}(\d[\d.,]*)\s*([a-záéíóúüñ/]+\.?)?\s*$",
        re.I | re.M,
    ),
    # "Ítem 1: Tóner HP LaserJet — 10 unidades"
    re.compile(
        r"[íi]tem\s+(\d+)[:\-]?\s+(.+?)[—\-–]\s*(\d[\d.,]*)\s*([a-záéíóúüñ]+)?",
        re.I,
    ),
    # Línea con cantidad al inicio: "   200   Hojas A4 80gr"
    re.compile(
        r"^\s+(\d[\d.,]*)\s+([a-záéíóúüñ][^\n]{5,})",
        re.I | re.M,
    ),
]

# Patrones para documentos de contratación directa / texto libre
_PROSE_PATTERNS = [
    # "Objeto de la Contratación: Compra de 2 (dos) armarios metálicos..."
    re.compile(
        r"objeto\s+de\s+la\s+contrataci[oó]n\s*:\s*(.+?)(?:\.|$)",
        re.I | re.S,
    ),
    # "Compra de 2 (dos) armarios metálicos..."
    re.compile(
        r"compra\s+de\s+(\d+)\s+\([^)]+\)\s+(.+?)(?:\.|para|cuyo|en el)",
        re.I,
    ),
    # "Adquisición de 10 (diez) resmas de papel..."
    re.compile(
        r"adquisici[oó]n\s+de\s+(\d+)\s+\([^)]+\)\s+(.+?)(?:\.|para|cuyo)",
        re.I,
    ),
    # "Contratación del servicio de..."
    re.compile(
        r"contrataci[oó]n\s+del?\s+(.+?)(?:\.|para|cuyo|$)",
        re.I,
    ),
]


def _numero_escrito_a_digito(texto: str) -> float | None:
    """Extrae número inicial de un texto como '2 (dos) armarios' → 2.0"""
    m = re.match(r"(\d+)", texto.strip())
    return float(m.group(1)) if m else None


def extraer_items_prosa(pdf_path: Path) -> list[dict]:
    """Extrae ítems de documentos de contratación directa con texto libre."""
    items = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)

        # Patrón 1: "Objeto de la Contratación: ..."
        m = re.search(
            r"[Oo]bjeto\s+de\s+la\s+[Cc]ontrataci[oó]n\s*:\s*(.+?)(?:\n[A-Z]|Fecha|IMPORTANTE|$)",
            texto, re.S
        )
        if m:
            desc_completa = limpiar(m.group(1))
            # Intentar extraer cantidad del inicio: "Compra de 2 (dos) X"
            cant_m = re.search(r"de\s+(\d+)\s+\([^)]+\)", desc_completa, re.I)
            cantidad = float(cant_m.group(1)) if cant_m else 1.0

            # Limpiar descripción quitando "Compra de N (N) "
            desc_limpia = re.sub(r"^(compra|adquisici[oó]n|contrataci[oó]n)\s+de\s+\d+\s+\([^)]+\)\s*", "", desc_completa, flags=re.I)
            if not desc_limpia:
                desc_limpia = desc_completa

            items.append({
                "numero": "1",
                "descripcion": desc_limpia.strip(". "),
                "cantidad": cantidad,
                "unidad": "unidad",
                "precio_referencia": None,
                "fuente_precio": None,
            })
            return items

        # Patrón 2: "Compra/Adquisición de N (N) descripción"
        for pat in _PROSE_PATTERNS[1:3]:
            for match in pat.finditer(texto):
                grupos = match.groups()
                if len(grupos) == 2:
                    cantidad = float(grupos[0])
                    descripcion = limpiar(grupos[1])
                elif len(grupos) == 1:
                    descripcion = limpiar(grupos[0])
                    cantidad = 1.0
                else:
                    continue
                if descripcion and len(descripcion) > 5:
                    items.append({
                        "numero": str(len(items) + 1),
                        "descripcion": descripcion,
                        "cantidad": cantidad,
                        "unidad": "unidad",
                        "precio_referencia": None,
                        "fuente_precio": None,
                    })

    except Exception as e:
        log.error("Error en extracción por prosa: %s", e)

    return items


def extraer_items_texto(pdf_path: Path) -> list[dict]:
    """Fallback: extrae ítems mediante patrones regex en texto plano."""
    items = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            texto_total = "\n".join(
                p.extract_text() or "" for p in pdf.pages
            )

        for pat in _ITEM_PATTERNS:
            for m in pat.finditer(texto_total):
                grupos = m.groups()
                if len(grupos) >= 2:
                    numero   = limpiar(grupos[0]) if grupos[0].isdigit() else ""
                    desc_raw = grupos[1] if not grupos[0].isdigit() else grupos[1]
                    descripcion = limpiar(desc_raw)
                    if not descripcion or len(descripcion) < 4:
                        continue

                    cant_raw = grupos[2] if len(grupos) > 2 else None
                    cantidad = parsear_numero(cant_raw) if cant_raw else None
                    unidad   = limpiar(grupos[3]) if len(grupos) > 3 and grupos[3] else ""

                    # Evitar duplicados por descripción
                    if not any(i["descripcion"] == descripcion for i in items):
                        items.append({
                            "numero": numero,
                            "descripcion": descripcion,
                            "cantidad": cantidad,
                            "unidad": unidad,
                            "precio_referencia": None,
                            "fuente_precio": None,
                        })
    except Exception as e:
        log.error("Error en extracción por texto: %s", e)

    return items


# ── Main ──────────────────────────────────────────────────────────────────────

def procesar_licitacion(lic: dict) -> dict:
    pdf_local = lic.get("pliego_pdf_local")
    if not pdf_local:
        log.warning("[%s] Sin PDF descargado, saltando", lic["id"])
        return lic

    pdf_path = BASE_DIR / pdf_local
    if not pdf_path.exists():
        log.warning("[%s] PDF no encontrado en disco: %s", lic["id"], pdf_path)
        return lic

    log.info("[%s] Procesando PDF: %s", lic["id"], pdf_path.name)

    # Intentar tabla primero, luego texto estructurado, luego prosa
    items = extraer_items_tabla(pdf_path)
    metodo = "tabla"

    if not items:
        log.info("[%s] Sin tablas, probando extracción por texto estructurado", lic["id"])
        items = extraer_items_texto(pdf_path)
        metodo = "texto"

    if not items:
        log.info("[%s] Sin texto estructurado, probando extracción por prosa", lic["id"])
        items = extraer_items_prosa(pdf_path)
        metodo = "prosa"

    if items:
        log.info("[%s] %d ítems extraídos (método: %s)", lic["id"], len(items), metodo)
        lic["items"] = items
    else:
        log.warning("[%s] No se pudieron extraer ítems del PDF", lic["id"])

    return lic


def main():
    from datetime import datetime
    log.info("=" * 60)
    log.info("Extractor de ítems — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    licitaciones = load_licitaciones()
    if not licitaciones:
        print("❌ No hay licitaciones. Ejecutá primero: python scraper.py")
        return

    procesadas = 0
    for i, lic in enumerate(licitaciones):
        if lic.get("items"):  # ya tiene ítems, saltar
            log.info("[%s] Ya tiene %d ítems, saltando", lic["id"], len(lic["items"]))
            continue
        licitaciones[i] = procesar_licitacion(lic)
        if licitaciones[i].get("items"):
            procesadas += 1

    save_licitaciones(licitaciones)
    print(f"\n[OK] Extraccion completada. {procesadas} licitaciones con items nuevos.")


if __name__ == "__main__":
    main()
