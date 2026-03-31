#!/usr/bin/env python3
"""
Módulo 1 — Scraper de Licitaciones
Marc Chagall Laboratorio Fotográfico — Salta, Argentina

Fuentes:
  - UNSA DGOyS
  - UNSA Rectorado
  - SaltaCompra (público)
"""

import json
import os
import re
import time
import logging
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

# ── Configuración de logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Rutas y archivos ────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
PLIEGOS_DIR = BASE_DIR / "pliegos"
LICITACIONES_FILE = BASE_DIR / "licitaciones.json"
CONFIG_FILE = BASE_DIR / "config.json"

PLIEGOS_DIR.mkdir(exist_ok=True)
(BASE_DIR / "logs").mkdir(exist_ok=True)

# ── Carga de config ─────────────────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
DELAY = CONFIG.get("delay_entre_requests", 1.5)

# ── Helpers HTTP ─────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def get(url: str, timeout: int = 20, **kwargs) -> requests.Response | None:
    """GET con manejo de errores y delay."""
    try:
        resp = SESSION.get(url, timeout=timeout, **kwargs)
        resp.raise_for_status()
        time.sleep(DELAY)
        return resp
    except requests.RequestException as e:
        log.error("Error GET %s → %s", url, e)
        return None


def robots_allowed(url: str) -> bool:
    """Verifica robots.txt antes de scrapear."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp.can_fetch(HEADERS["User-Agent"], url)
    except Exception:
        return True  # si no hay robots.txt, proceder


# ── Persistencia de licitaciones ─────────────────────────────────────────────

def load_licitaciones() -> list[dict]:
    if LICITACIONES_FILE.exists():
        with open(LICITACIONES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_licitaciones(data: list[dict]) -> None:
    with open(LICITACIONES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("licitaciones.json actualizado (%d registros)", len(data))


def merge_licitaciones(existentes: list[dict], nuevas: list[dict]) -> list[dict]:
    """Une listas usando el id como clave; no sobreescribe cotizaciones ya cargadas."""
    by_id = {l["id"]: l for l in existentes}
    for nueva in nuevas:
        eid = nueva["id"]
        if eid in by_id:
            # Conserva campos ya procesados
            for conservar in ("items", "cotizacion", "pliego_pdf_local"):
                if by_id[eid].get(conservar):
                    nueva[conservar] = by_id[eid][conservar]
            by_id[eid].update(nueva)
        else:
            by_id[eid] = nueva
    return list(by_id.values())


# ── Helpers de ID y fechas ────────────────────────────────────────────────────

def make_id(organismo_slug: str, titulo: str, extra: str = "") -> str:
    raw = f"{organismo_slug}-{titulo}-{extra}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


_MONTHS_ES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "setiembre": "09", "octubre": "10",
    "noviembre": "11", "diciembre": "12",
}

def parse_fecha(texto: str) -> str | None:
    """Intenta parsear fechas en varios formatos y devuelve ISO 8601."""
    if not texto:
        return None
    texto = texto.strip().lower()
    # Normalizar año con punto: "2.026" → "2026"
    texto = re.sub(r"\b(2\.0\d{2})\b", lambda x: x.group(1).replace(".", ""), texto)

    # DD/MM/YYYY HH:MM o DD-MM-YYYY HH:MM
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})\s*(\d{2}:\d{2})?", texto)
    if m:
        d, mo, y = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
        t = m.group(4) or "00:00"
        return f"{y}-{mo}-{d}T{t}:00"

    # "5 de marzo de 2026 a las 11:00"
    m = re.search(
        r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})(?:.*?(\d{2}:\d{2}))?", texto
    )
    if m:
        d = m.group(1).zfill(2)
        mo = _MONTHS_ES.get(m.group(2), "01")
        y = m.group(3)
        t = m.group(4) or "00:00"
        return f"{y}-{mo}-{d}T{t}:00"

    return None


# ── Descarga de PDFs ──────────────────────────────────────────────────────────

def download_pdf(url: str, nombre: str) -> str | None:
    """Descarga un PDF y devuelve la ruta local relativa."""
    if not url:
        return None
    nombre_limpio = re.sub(r"[^\w\-]", "_", nombre)[:80]
    dest = PLIEGOS_DIR / f"{nombre_limpio}.pdf"
    if dest.exists():
        log.info("PDF ya descargado: %s", dest.name)
        return f"pliegos/{dest.name}"
    resp = get(url)
    if resp and resp.headers.get("content-type", "").startswith("application/pdf"):
        dest.write_bytes(resp.content)
        log.info("PDF descargado: %s", dest.name)
        return f"pliegos/{dest.name}"
    log.warning("No se pudo descargar PDF desde %s", url)
    return None


# ── SCRAPER 1 — UNSA DGOyS ────────────────────────────────────────────────────

def scrape_unsa_dgoys() -> list[dict]:
    URL = "https://www.unsa.edu.ar/licitaciones-y-compras-dgoys/"
    log.info("Scrapeando UNSA DGOyS: %s", URL)
    if not robots_allowed(URL):
        log.warning("robots.txt no permite scrapear %s", URL)
        return []

    resp = get(URL)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    resultados = []

    # WordPress típicamente muestra licitaciones como posts o entradas de lista
    # Buscamos <article>, <li> o <div> con links a PDF y texto de licitación
    entradas = (
        soup.select("article")
        or soup.select(".entry-content li")
        or soup.select(".post-content li")
        or soup.select("main li")
    )

    if not entradas:
        # Fallback: buscar todos los links que apunten a PDF
        entradas = [a.parent for a in soup.find_all("a", href=re.compile(r"\.pdf", re.I))]

    seen = set()
    for entrada in entradas:
        try:
            titulo_tag = (
                entrada.find(["h1", "h2", "h3", "h4", "strong"])
                or entrada
            )
            titulo = titulo_tag.get_text(separator=" ", strip=True)
            if not titulo or len(titulo) < 5:
                continue

            # Expediente
            exp_match = re.search(
                r"(exp(?:ediente)?\.?\s*n[°ºo]?\s*[\w/\-]+)", titulo, re.I
            )
            expediente = exp_match.group(0) if exp_match else ""

            # Fecha apertura
            texto_completo = entrada.get_text(" ", strip=True)
            fecha_str = None
            for pat in [
                r"apertura[:\s]+([^|<\n]+)",
                r"(\d{1,2}[/-]\d{1,2}[/-]\d{4}\s*\d{2}:\d{2})",
                r"(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
            ]:
                m = re.search(pat, texto_completo, re.I)
                if m:
                    fecha_str = parse_fecha(m.group(1))
                    if fecha_str:
                        break

            # Link al PDF
            pdf_tag = entrada.find("a", href=re.compile(r"\.pdf", re.I))
            pliego_url = urljoin(URL, pdf_tag["href"]) if pdf_tag else None

            uid = make_id("unsa-dgoys", titulo)
            if uid in seen:
                continue
            seen.add(uid)

            pdf_local = None
            if pliego_url:
                pdf_local = download_pdf(pliego_url, uid)

            resultados.append({
                "id": uid,
                "organismo": "UNSA DGOyS",
                "titulo": titulo,
                "expediente": expediente,
                "fecha_apertura": fecha_str,
                "pliego_url": pliego_url,
                "pliego_pdf_local": pdf_local,
                "estado": "activa",
                "items": [],
                "cotizacion": None,
            })
        except Exception as e:
            log.error("Error procesando entrada DGOyS: %s", e)

    log.info("UNSA DGOyS: %d licitaciones encontradas", len(resultados))
    return resultados


# ── SCRAPER 2 — UNSA Rectorado ────────────────────────────────────────────────

def scrape_unsa_rectorado() -> list[dict]:
    URL = "https://www.unsa.edu.ar/licitaciones-y-compras/"
    log.info("Scrapeando UNSA Rectorado: %s", URL)
    if not robots_allowed(URL):
        log.warning("robots.txt no permite scrapear %s", URL)
        return []

    resp = get(URL)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    resultados = []

    entradas = (
        soup.select("article")
        or soup.select(".entry-content li")
        or soup.select(".post-content li")
        or soup.select("main li")
    )

    if not entradas:
        entradas = [a.parent for a in soup.find_all("a", href=re.compile(r"\.pdf", re.I))]

    seen = set()
    for entrada in entradas:
        try:
            titulo_tag = (
                entrada.find(["h1", "h2", "h3", "h4", "strong"])
                or entrada
            )
            titulo = titulo_tag.get_text(separator=" ", strip=True)
            if not titulo or len(titulo) < 5:
                continue

            exp_match = re.search(
                r"(exp(?:ediente)?\.?\s*n[°ºo]?\s*[\w/\-]+)", titulo, re.I
            )
            expediente = exp_match.group(0) if exp_match else ""

            texto_completo = entrada.get_text(" ", strip=True)
            fecha_str = None
            for pat in [
                r"apertura[:\s]+([^|<\n]+)",
                r"(\d{1,2}[/-]\d{1,2}[/-]\d{4}\s*\d{2}:\d{2})",
                r"(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
            ]:
                m = re.search(pat, texto_completo, re.I)
                if m:
                    fecha_str = parse_fecha(m.group(1))
                    if fecha_str:
                        break

            pdf_tag = entrada.find("a", href=re.compile(r"\.pdf", re.I))
            pliego_url = urljoin(URL, pdf_tag["href"]) if pdf_tag else None

            uid = make_id("unsa-rect", titulo)
            if uid in seen:
                continue
            seen.add(uid)

            pdf_local = None
            if pliego_url:
                pdf_local = download_pdf(pliego_url, uid)

            resultados.append({
                "id": uid,
                "organismo": "UNSA Rectorado",
                "titulo": titulo,
                "expediente": expediente,
                "fecha_apertura": fecha_str,
                "pliego_url": pliego_url,
                "pliego_pdf_local": pdf_local,
                "estado": "activa",
                "items": [],
                "cotizacion": None,
            })
        except Exception as e:
            log.error("Error procesando entrada Rectorado: %s", e)

    log.info("UNSA Rectorado: %d licitaciones encontradas", len(resultados))
    return resultados


# ── SCRAPER 3 — SaltaCompra ───────────────────────────────────────────────────

def scrape_salta_compra() -> list[dict]:
    BASE = "https://saltacompra.gob.ar"
    # Procesos con apertura próxima y últimos 30 días
    URLS_INTENTAR = [
        f"{BASE}/Compras.aspx?qs=W1HXHGHtH10=",   # apertura próxima
        f"{BASE}/Compras.aspx?qs=iouVZE0yWCs=",   # últimos 30 días
        f"{BASE}/BuscarAvanzado.aspx",
        f"{BASE}/Compras.aspx",
    ]
    log.info("Scrapeando SaltaCompra: %s", BASE)

    if not robots_allowed(BASE):
        log.warning("robots.txt no permite scrapear %s", BASE)
        return []

    soup = None
    url_exitosa = None
    for url in URLS_INTENTAR:
        resp = get(url)
        if resp:
            soup = BeautifulSoup(resp.text, "lxml")
            # Verificar si tiene contenido de compras/licitaciones
            if soup.find(string=re.compile(r"licitac|proceso|compra", re.I)):
                url_exitosa = url
                log.info("SaltaCompra: URL exitosa → %s", url)
                break

    if not soup or not url_exitosa:
        log.warning("SaltaCompra: no se pudo acceder a ningún endpoint conocido")
        return []

    resultados = []
    seen = set()

    # Buscar filas de tabla típicas
    rows = soup.select("table tbody tr") or soup.select(".licitacion-item")

    for row in rows:
        try:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            texto_fila = row.get_text(" ", strip=True)
            titulo = cells[0].get_text(strip=True) or cells[1].get_text(strip=True)
            if not titulo or len(titulo) < 5:
                continue

            exp_match = re.search(r"[\w/\-]+/\d{2,4}", texto_fila)
            expediente = exp_match.group(0) if exp_match else ""

            fecha_str = None
            for cell in cells:
                txt = cell.get_text(strip=True)
                fecha_str = parse_fecha(txt)
                if fecha_str:
                    break

            pdf_tag = row.find("a", href=re.compile(r"\.pdf", re.I))
            pliego_url = urljoin(url_exitosa, pdf_tag["href"]) if pdf_tag else None

            uid = make_id("saltacompra", titulo)
            if uid in seen:
                continue
            seen.add(uid)

            pdf_local = None
            if pliego_url:
                pdf_local = download_pdf(pliego_url, uid)

            resultados.append({
                "id": uid,
                "organismo": "SaltaCompra",
                "titulo": titulo,
                "expediente": expediente,
                "fecha_apertura": fecha_str,
                "pliego_url": pliego_url,
                "pliego_pdf_local": pdf_local,
                "estado": "activa",
                "items": [],
                "cotizacion": None,
            })
        except Exception as e:
            log.error("Error procesando fila SaltaCompra: %s", e)

    log.info("SaltaCompra: %d licitaciones encontradas", len(resultados))
    return resultados


# ── SCRAPER 4 — Municipalidad de Salta ───────────────────────────────────────

def scrape_municipalidad_salta() -> list[dict]:
    BASE_URL = "https://municipalidadsalta.gob.ar/contrataciones/"
    CATEGORIAS = [
        BASE_URL,
        "https://municipalidadsalta.gob.ar/oficina-contrataciones/obras-publicas-licitaciones/",
        "https://municipalidadsalta.gob.ar/oficina-contrataciones/hacienda-licitaciones/",
        "https://municipalidadsalta.gob.ar/oficina-contrataciones/servicios-publicos-licitacion/",
    ]
    log.info("Scrapeando Municipalidad de Salta: %s", BASE_URL)

    if not robots_allowed(BASE_URL):
        log.warning("robots.txt no permite scrapear %s", BASE_URL)
        return []

    resultados = []
    seen = set()

    for cat_url in CATEGORIAS:
        # Paginar hasta 5 páginas por categoría
        for pagina in range(1, 6):
            url = cat_url if pagina == 1 else f"{cat_url}page/{pagina}/"
            resp = get(url)
            if not resp:
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # Artículos de licitación — WordPress con Elementor
            articulos = (
                soup.select(".void-post-grid article")
                or soup.select("article.post")
                or soup.select(".post-grid-item")
                or soup.select("main article")
            )

            if not articulos:
                break  # Sin más resultados en esta categoría

            hay_nuevos = False
            for articulo in articulos:
                try:
                    # Título
                    titulo_tag = articulo.find(["h2", "h3", "h4"], class_=re.compile(r"entry-title|post-title", re.I))
                    if not titulo_tag:
                        titulo_tag = articulo.find(["h2", "h3", "h4"])
                    if not titulo_tag:
                        continue
                    titulo = limpiar(titulo_tag.get_text())
                    if not titulo or len(titulo) < 5:
                        continue

                    # Link a la página de detalle
                    link_tag = titulo_tag.find("a") or articulo.find("a", href=re.compile(r"municipalidadsalta"))
                    detalle_url = link_tag["href"] if link_tag else None

                    # Expediente desde el título
                    exp_match = re.search(
                        r"(licitaci[oó]n\s+(?:p[uú]blica\s+)?n[°º]?\s*[\d/\-]+|"
                        r"exp(?:ediente)?\.?\s*n[°º]?\s*[\w/\-]+|"
                        r"n[°º]\s*[\d]+[/-]\d{2,4})",
                        titulo, re.I
                    )
                    expediente = exp_match.group(0) if exp_match else ""

                    # Fecha desde el meta o texto
                    texto = articulo.get_text(" ", strip=True)
                    fecha_str = None
                    for pat in [
                        r"(\d{1,2}[/-]\d{1,2}[/-]\d{4}\s*\d{2}:\d{2})",
                        r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
                        r"(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
                    ]:
                        m = re.search(pat, texto, re.I)
                        if m:
                            fecha_str = parse_fecha(m.group(1))
                            if fecha_str:
                                break

                    # Si hay URL de detalle, buscar PDF ahí
                    pliego_url = None
                    if detalle_url:
                        resp_det = get(detalle_url)
                        if resp_det:
                            soup_det = BeautifulSoup(resp_det.text, "lxml")
                            pdf_tag = soup_det.find("a", href=re.compile(r"\.pdf", re.I))
                            if pdf_tag:
                                pliego_url = urljoin(detalle_url, pdf_tag["href"])
                            # Intentar extraer fecha de la página de detalle si no la tenemos
                            if not fecha_str:
                                texto_det = soup_det.get_text(" ", strip=True)
                                for pat in [
                                    r"apertura[:\s]+([^|\n<]{5,30})",
                                    r"(\d{1,2}[/-]\d{1,2}[/-]\d{4}\s*\d{2}:\d{2})",
                                    r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
                                ]:
                                    m = re.search(pat, texto_det, re.I)
                                    if m:
                                        fecha_str = parse_fecha(m.group(1))
                                        if fecha_str:
                                            break

                    uid = make_id("municipalidad-salta", titulo)
                    if uid in seen:
                        continue
                    seen.add(uid)
                    hay_nuevos = True

                    pdf_local = None
                    if pliego_url:
                        pdf_local = download_pdf(pliego_url, uid)

                    resultados.append({
                        "id": uid,
                        "organismo": "Municipalidad de Salta",
                        "titulo": titulo,
                        "expediente": expediente,
                        "fecha_apertura": fecha_str,
                        "pliego_url": pliego_url or detalle_url,
                        "pliego_pdf_local": pdf_local,
                        "estado": "activa",
                        "items": [],
                        "cotizacion": None,
                    })

                except Exception as e:
                    log.error("Error procesando artículo Municipalidad: %s", e)

            if not hay_nuevos:
                break  # No hay licitaciones nuevas en esta página

    log.info("Municipalidad de Salta: %d licitaciones encontradas", len(resultados))
    return resultados


def limpiar(texto: str) -> str:
    """Limpia texto de espacios y saltos de línea."""
    return re.sub(r"\s+", " ", texto or "").strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("Iniciando scraper — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    nuevas: list[dict] = []

    for scraper_fn in [scrape_unsa_dgoys, scrape_unsa_rectorado, scrape_salta_compra, scrape_municipalidad_salta]:
        try:
            resultados = scraper_fn()
            nuevas.extend(resultados)
        except Exception as e:
            log.error("Error en scraper %s: %s", scraper_fn.__name__, e)

    existentes = load_licitaciones()
    combinadas = merge_licitaciones(existentes, nuevas)
    save_licitaciones(combinadas)

    log.info("Scraping finalizado. Total licitaciones: %d", len(combinadas))
    print(f"\n[OK] Scraping completado. {len(nuevas)} licitaciones encontradas. "
          f"Total en archivo: {len(combinadas)}")


if __name__ == "__main__":
    main()
