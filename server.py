#!/usr/bin/env python3
"""
Módulo 6 — Servidor Flask Mínimo
Marc Chagall Laboratorio Fotográfico — Salta, Argentina

Endpoints:
  GET  /licitaciones        → devuelve licitaciones.json
  GET  /alertas             → devuelve alertas.json
  GET  /config              → devuelve config.json (sin credenciales)
  POST /config              → actualiza config.json
  POST /cotizar/<id>        → ejecuta cotizador para una licitación
  GET  /descargar/<id>      → descarga el Excel generado
  POST /scrape              → ejecuta el scraper
  POST /extraer/<id>        → ejecuta extractor de ítems para una licitación
"""

import json
import logging
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
LICITACIONES_FILE = BASE_DIR / "licitaciones.json"
ALERTAS_FILE = BASE_DIR / "alertas.json"
CONFIG_FILE = BASE_DIR / "config.json"
COTIZACIONES_DIR = BASE_DIR / "cotizaciones"

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")
CORS(app)

# ── Helpers ───────────────────────────────────────────────────────────────────

def read_json(path: Path, default=None):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else []


def write_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Lock para evitar ejecuciones simultáneas de scrapers
_scrape_lock = threading.Lock()


# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Sirve el dashboard principal."""
    return app.send_static_file("dashboard.html")


@app.route("/licitaciones", methods=["GET"])
def get_licitaciones():
    data = read_json(LICITACIONES_FILE)
    # Soporte filtros opcionales por query string
    estado = request.args.get("estado")
    organismo = request.args.get("organismo", "").lower()
    if estado:
        data = [l for l in data if l.get("estado") == estado]
    if organismo:
        data = [l for l in data if organismo in l.get("organismo", "").lower()]
    return jsonify(data)


@app.route("/licitaciones/<lic_id>", methods=["GET"])
def get_licitacion(lic_id: str):
    data = read_json(LICITACIONES_FILE)
    lic = next((l for l in data if l["id"] == lic_id), None)
    if not lic:
        abort(404, description=f"Licitación '{lic_id}' no encontrada")
    return jsonify(lic)


@app.route("/alertas", methods=["GET"])
def get_alertas():
    # Regenerar alertas en tiempo real
    try:
        import alertas as alertas_mod
        resultado = alertas_mod.main()
        return jsonify(resultado or [])
    except Exception as e:
        log.error("Error generando alertas: %s", e)
        return jsonify(read_json(ALERTAS_FILE))


@app.route("/config", methods=["GET"])
def get_config():
    cfg = read_json(CONFIG_FILE, {})
    # No devolver credenciales al frontend
    cfg_safe = {k: v for k, v in cfg.items() if "password" not in k.lower()}
    return jsonify(cfg_safe)


@app.route("/config", methods=["POST"])
def update_config():
    nueva_config = request.get_json()
    if not nueva_config:
        return jsonify({"error": "Cuerpo JSON requerido"}), 400

    cfg_actual = read_json(CONFIG_FILE, {})

    # Actualizar solo campos permitidos (no sobreescribir password si viene vacío)
    campos_protegidos = {"smtp_password"}
    for k, v in nueva_config.items():
        if k in campos_protegidos and not v:
            continue
        cfg_actual[k] = v

    write_json(CONFIG_FILE, cfg_actual)
    log.info("Config actualizado: %s", list(nueva_config.keys()))
    return jsonify({"ok": True, "mensaje": "Configuración guardada"})


@app.route("/cotizar/<lic_id>", methods=["POST"])
def cotizar(lic_id: str):
    body = request.get_json() or {}
    margen = body.get("margen")
    force  = body.get("force", False)

    cmd = [sys.executable, str(BASE_DIR / "cotizador.py"), "--id", lic_id]
    if margen is not None:
        cmd += ["--margen", str(margen)]
    if force:
        cmd.append("--force")

    log.info("Ejecutando cotizador para %s", lic_id)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(BASE_DIR), timeout=120
        )
        if result.returncode != 0:
            log.error("cotizador.py stderr: %s", result.stderr)
            return jsonify({"error": result.stderr or "Error en cotizador"}), 500

        # Devolver datos actualizados
        lics = read_json(LICITACIONES_FILE)
        lic = next((l for l in lics if l["id"] == lic_id), None)
        return jsonify({"ok": True, "licitacion": lic, "stdout": result.stdout})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout: la búsqueda de precios tardó demasiado"}), 504
    except Exception as e:
        log.error("Error cotizando %s: %s", lic_id, e)
        return jsonify({"error": str(e)}), 500


@app.route("/descargar/<lic_id>", methods=["GET"])
def descargar(lic_id: str):
    lics = read_json(LICITACIONES_FILE)
    lic = next((l for l in lics if l["id"] == lic_id), None)
    if not lic:
        abort(404, description="Licitación no encontrada")

    excel_rel = lic.get("cotizacion", {}).get("excel") if lic.get("cotizacion") else None
    if not excel_rel:
        abort(404, description="No hay cotización Excel para esta licitación")

    excel_path = BASE_DIR / excel_rel
    if not excel_path.exists():
        abort(404, description=f"Archivo no encontrado: {excel_rel}")

    return send_file(
        excel_path,
        as_attachment=True,
        download_name=excel_path.name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/scrape", methods=["POST"])
def scrape():
    if not _scrape_lock.acquire(blocking=False):
        return jsonify({"error": "Scraper ya en ejecución"}), 409

    def run_scraper():
        try:
            result = subprocess.run(
                [sys.executable, str(BASE_DIR / "scraper.py")],
                capture_output=True, text=True,
                cwd=str(BASE_DIR), timeout=300,
            )
            log.info("Scraper finalizado. returncode=%d", result.returncode)
        except Exception as e:
            log.error("Error en scraper background: %s", e)
        finally:
            _scrape_lock.release()

    t = threading.Thread(target=run_scraper, daemon=True)
    t.start()
    return jsonify({"ok": True, "mensaje": "Scraper iniciado en segundo plano"})


@app.route("/extraer/<lic_id>", methods=["POST"])
def extraer(lic_id: str):
    """Fuerza la extracción de ítems del PDF de una licitación específica."""
    try:
        import extraer_items as ext_mod
        lics = read_json(LICITACIONES_FILE)
        lic_idx = next((i for i, l in enumerate(lics) if l["id"] == lic_id), None)
        if lic_idx is None:
            return jsonify({"error": "Licitación no encontrada"}), 404

        # Limpiar ítems para forzar re-extracción
        lics[lic_idx]["items"] = []
        write_json(LICITACIONES_FILE, lics)

        lics[lic_idx] = ext_mod.procesar_licitacion(lics[lic_idx])
        write_json(LICITACIONES_FILE, lics)

        return jsonify({
            "ok": True,
            "items_encontrados": len(lics[lic_idx].get("items", [])),
            "licitacion": lics[lic_idx],
        })
    except Exception as e:
        log.error("Error extrayendo ítems %s: %s", lic_id, e)
        return jsonify({"error": str(e)}), 500


@app.route("/stats", methods=["GET"])
def stats():
    """Resumen estadístico para el dashboard."""
    lics = read_json(LICITACIONES_FILE)
    hoy = datetime.now()

    def dias_restantes(lic):
        fa = lic.get("fecha_apertura")
        if not fa:
            return None
        try:
            from datetime import datetime as dt
            return (dt.fromisoformat(fa) - hoy).days
        except Exception:
            return None

    return jsonify({
        "total": len(lics),
        "activas": sum(1 for l in lics if l.get("estado") == "activa"),
        "vencidas": sum(1 for l in lics if l.get("estado") == "vencida"),
        "con_cotizacion": sum(1 for l in lics if l.get("cotizacion")),
        "con_items": sum(1 for l in lics if l.get("items")),
        "urgentes_3d": sum(
            1 for l in lics
            if l.get("estado") == "activa"
            and (dr := dias_restantes(l)) is not None
            and 0 < dr <= 3
        ),
        "alertas_7d": sum(
            1 for l in lics
            if l.get("estado") == "activa"
            and (dr := dias_restantes(l)) is not None
            and 3 < dr <= 7
        ),
    })


# ── Manejo de errores ─────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": str(e)}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Error interno del servidor"}), 500


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Servidor Marc Chagall Licitaciones")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  Marc Chagall — Servidor de Licitaciones")
    print(f"  http://{args.host}:{args.port}")
    print(f"{'='*55}\n")

    app.run(host=args.host, port=args.port, debug=args.debug)
