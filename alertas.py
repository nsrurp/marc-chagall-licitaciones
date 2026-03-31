#!/usr/bin/env python3
"""
Módulo 4 — Sistema de Alertas
Marc Chagall Laboratorio Fotográfico — Salta, Argentina

Evalúa vencimientos de licitaciones y envía notificaciones por email.
"""

import json
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from colorama import Fore, Style, init

init(autoreset=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/alertas.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
LICITACIONES_FILE = BASE_DIR / "licitaciones.json"
ALERTAS_FILE = BASE_DIR / "alertas.json"
CONFIG_FILE = BASE_DIR / "config.json"
(BASE_DIR / "logs").mkdir(exist_ok=True)


def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_licitaciones() -> list[dict]:
    if not LICITACIONES_FILE.exists():
        return []
    with open(LICITACIONES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_licitaciones(data: list[dict]) -> None:
    with open(LICITACIONES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_alertas(data: list[dict]) -> None:
    with open(ALERTAS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Evaluación de alertas ────────────────────────────────────────────────────

def parse_iso(fecha_str: str | None) -> datetime | None:
    if not fecha_str:
        return None
    try:
        return datetime.fromisoformat(fecha_str)
    except ValueError:
        return None


def evaluar_alerta(lic: dict, hoy: datetime) -> dict | None:
    """
    Evalúa una licitación y devuelve un dict de alerta si corresponde.
    Modifica el estado si está vencida.
    """
    if lic.get("estado") == "vencida":
        return None

    fecha = parse_iso(lic.get("fecha_apertura"))
    if not fecha:
        return None

    # Quitar tzinfo para comparación naive
    if fecha.tzinfo:
        hoy = hoy.replace(tzinfo=timezone.utc)

    delta_dias = (fecha - hoy).days

    if delta_dias <= 0:
        lic["estado"] = "vencida"
        return {
            "id": lic["id"],
            "organismo": lic.get("organismo", ""),
            "titulo": lic.get("titulo", ""),
            "expediente": lic.get("expediente", ""),
            "fecha_apertura": lic.get("fecha_apertura", ""),
            "dias_restantes": delta_dias,
            "nivel": "vencida",
            "emoji": "⛔",
            "mensaje": f"VENCIDA (apertura: {lic.get('fecha_apertura', '')[:10]})",
            "cotizacion_lista": bool(lic.get("cotizacion")),
        }
    elif delta_dias <= 3:
        return {
            "id": lic["id"],
            "organismo": lic.get("organismo", ""),
            "titulo": lic.get("titulo", ""),
            "expediente": lic.get("expediente", ""),
            "fecha_apertura": lic.get("fecha_apertura", ""),
            "dias_restantes": delta_dias,
            "nivel": "critica",
            "emoji": "🚨",
            "mensaje": f"URGENTE — {delta_dias} días para la apertura",
            "cotizacion_lista": bool(lic.get("cotizacion")),
        }
    elif delta_dias <= 7:
        return {
            "id": lic["id"],
            "organismo": lic.get("organismo", ""),
            "titulo": lic.get("titulo", ""),
            "expediente": lic.get("expediente", ""),
            "fecha_apertura": lic.get("fecha_apertura", ""),
            "dias_restantes": delta_dias,
            "nivel": "advertencia",
            "emoji": "⚠️",
            "mensaje": f"1 semana — {delta_dias} días para la apertura",
            "cotizacion_lista": bool(lic.get("cotizacion")),
        }
    return None


# ── Imprimir en consola ───────────────────────────────────────────────────────

def imprimir_resumen(alertas: list[dict], total_activas: int, total_con_cot: int) -> None:
    print()
    print(Fore.YELLOW + Style.BRIGHT + "=" * 65)
    print(Fore.YELLOW + Style.BRIGHT + "  MARC CHAGALL — SISTEMA DE ALERTAS DE LICITACIONES")
    print(Fore.YELLOW + Style.BRIGHT + "=" * 65)
    print(f"  Fecha: {Fore.WHITE}{datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"  Licitaciones activas:   {Fore.GREEN}{total_activas}")
    print(f"  Con cotización lista:   {Fore.GREEN}{total_con_cot}")
    print(f"  Alertas generadas:      {Fore.CYAN}{len(alertas)}")
    print(Fore.YELLOW + "=" * 65)

    if not alertas:
        print(Fore.GREEN + "\n  [OK] Sin alertas urgentes. Todo en orden.")
        return

    # Agrupar por nivel
    criticas   = [a for a in alertas if a["nivel"] == "critica"]
    advertencias = [a for a in alertas if a["nivel"] == "advertencia"]
    vencidas   = [a for a in alertas if a["nivel"] == "vencida"]

    for grupo, color, titulo in [
        (criticas,     Fore.RED,    "🚨 CRÍTICAS (3 días o menos)"),
        (advertencias, Fore.YELLOW, "⚠️  ADVERTENCIAS (7 días o menos)"),
        (vencidas,     Fore.WHITE,  "⛔ VENCIDAS (hoy)"),
    ]:
        if not grupo:
            continue
        print(f"\n{color}{Style.BRIGHT}  {titulo}")
        print(color + "  " + "-" * 61)
        for a in grupo:
            cot_str = Fore.GREEN + "✅ CON COT." if a["cotizacion_lista"] else Fore.RED + "❌ SIN COT."
            print(
                f"{color}  {a['emoji']} {a['organismo'][:20]:<20} "
                f"{Fore.WHITE}{a['titulo'][:30]:<30} "
                f"{color}{a['dias_restantes']:+3d}d  {cot_str}"
            )

    print()


# ── Email de alertas ──────────────────────────────────────────────────────────

def _html_alertas(alertas: list[dict], empresa: str) -> str:
    filas = ""
    for a in alertas:
        color_nivel = {
            "critica": "#E74C3C",
            "advertencia": "#F39C12",
            "vencida": "#7F8C8D",
        }.get(a["nivel"], "#999")

        cot = "✅ Sí" if a["cotizacion_lista"] else "❌ No"
        filas += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #2a2d35;">{a['emoji']}</td>
          <td style="padding:8px;border-bottom:1px solid #2a2d35;">{a['organismo']}</td>
          <td style="padding:8px;border-bottom:1px solid #2a2d35;">{a['titulo'][:60]}</td>
          <td style="padding:8px;border-bottom:1px solid #2a2d35;color:{color_nivel};">
            {a['mensaje']}
          </td>
          <td style="padding:8px;border-bottom:1px solid #2a2d35;text-align:center;">{cot}</td>
        </tr>"""

    return f"""
    <html><body style="background:#0A0C10;color:#E8E8E8;font-family:Arial,sans-serif;margin:0;padding:20px;">
      <div style="max-width:800px;margin:0 auto;">
        <h1 style="color:#C8A84B;text-align:center;">{empresa}</h1>
        <h2 style="color:#E8E8E8;text-align:center;">Alerta de Licitaciones</h2>
        <p style="color:#888;">Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
        <table style="width:100%;border-collapse:collapse;background:#13161E;">
          <thead>
            <tr style="background:#C8A84B;color:#0A0C10;">
              <th style="padding:10px;">!</th>
              <th style="padding:10px;">Organismo</th>
              <th style="padding:10px;">Título</th>
              <th style="padding:10px;">Estado</th>
              <th style="padding:10px;">Cotización</th>
            </tr>
          </thead>
          <tbody>{filas}</tbody>
        </table>
        <p style="color:#555;font-size:12px;margin-top:20px;">
          Sistema de gestión de licitaciones — Marc Chagall Lab. Fotográfico
        </p>
      </div>
    </body></html>"""


def enviar_email(alertas: list[dict], config: dict) -> bool:
    email_dest = config.get("email_alertas", "")
    smtp_user  = config.get("smtp_gmail", "")
    smtp_pass  = config.get("smtp_password", "")

    if not all([email_dest, smtp_user, smtp_pass]):
        log.info("Email no configurado en config.json, saltando envío")
        return False

    empresa = config.get("empresa", "Marc Chagall")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚨 {len(alertas)} alertas de licitaciones — {empresa}"
    msg["From"] = smtp_user
    msg["To"] = email_dest

    texto_plano = "\n".join(
        f"{a['emoji']} {a['organismo']} | {a['titulo'][:50]} | {a['mensaje']}"
        for a in alertas
    )
    msg.attach(MIMEText(texto_plano, "plain", "utf-8"))
    msg.attach(MIMEText(_html_alertas(alertas, empresa), "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", config.get("smtp_port", 587)) as server:
            server.starttls(context=ctx)
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, email_dest, msg.as_string())
        log.info("Email de alertas enviado a %s", email_dest)
        print(Fore.GREEN + f"  📧 Email enviado a {email_dest}")
        return True
    except Exception as e:
        log.error("Error enviando email: %s", e)
        print(Fore.RED + f"  [ERROR] Error enviando email: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("Alertas — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    config = load_config()
    licitaciones = load_licitaciones()
    hoy = datetime.now()

    alertas = []
    for lic in licitaciones:
        alerta = evaluar_alerta(lic, hoy)
        if alerta:
            alertas.append(alerta)

    # Guardar licitaciones con estados actualizados (vencidas)
    save_licitaciones(licitaciones)
    save_alertas(alertas)

    # Estadísticas
    activas  = sum(1 for l in licitaciones if l.get("estado") == "activa")
    con_cot  = sum(1 for l in licitaciones if l.get("cotizacion"))

    # Imprimir consola
    imprimir_resumen(alertas, activas, con_cot)

    # Enviar email si hay alertas críticas/advertencias (no vencidas)
    alertas_enviar = [a for a in alertas if a["nivel"] in ("critica", "advertencia")]
    if alertas_enviar:
        enviar_email(alertas_enviar, config)

    log.info("Alertas generadas: %d", len(alertas))
    return alertas


if __name__ == "__main__":
    main()
