# Marc Chagall — Sistema de Gestión de Licitaciones

Sistema completo para buscar, cotizar y gestionar licitaciones públicas para **Marc Chagall Laboratorio Fotográfico**, Salta, Argentina.

---

## Estructura del proyecto

```
marc-chagall-licitaciones/
├── scraper.py          # Módulo 1: Scraping de licitaciones
├── extraer_items.py    # Módulo 2: Extracción de ítems desde PDFs
├── cotizador.py        # Módulo 3: Cotización automática con Excel
├── alertas.py          # Módulo 4: Sistema de alertas y emails
├── server.py           # Módulo 6: Servidor Flask (API REST)
├── dashboard.html      # Módulo 5: Dashboard web
├── config.json         # Configuración del sistema
├── requirements.txt    # Dependencias Python
├── licitaciones.json   # Base de datos (autogenerado)
├── alertas.json        # Alertas activas (autogenerado)
├── pliegos/            # PDFs descargados
├── cotizaciones/       # Excel generados
└── logs/               # Logs de cada módulo
```

---

## Instalación

### 1. Requisitos
- Python 3.10 o superior
- pip

### 2. Instalar dependencias

```bash
cd marc-chagall-licitaciones
pip install -r requirements.txt
```

---

## Uso paso a paso

### Paso 1 — Configurar el sistema

Editá `config.json` con los datos de la empresa:

```json
{
  "empresa": "Marc Chagall Laboratorio Fotográfico",
  "cuit": "30-XXXXXXXX-X",
  "margen_default": 25,
  "iva": 21,
  "email_alertas": "tu@email.com",
  "smtp_gmail": "tu@gmail.com",
  "smtp_password": "xxxx xxxx xxxx xxxx"
}
```

> Para el email: en Gmail generá una "Contraseña de aplicación" en
> Cuenta Google → Seguridad → Verificación en dos pasos → Contraseñas de apps

### Paso 2 — Scrapear licitaciones

```bash
python scraper.py
```

Descarga licitaciones de UNSA DGOyS, UNSA Rectorado y SaltaCompra.
Guarda los pliegos PDF en `pliegos/` y todo en `licitaciones.json`.

### Paso 3 — Extraer ítems de los pliegos

```bash
python extraer_items.py
```

Lee cada PDF y extrae la tabla de ítems (descripción, cantidad, unidad).

### Paso 4 — Generar cotización

```bash
python cotizador.py --id <ID_LICITACION> --margen 25
```

Busca precios en MercadoLibre y PrecioClaro, calcula precios de venta
y genera un Excel en `cotizaciones/`.

Para encontrar el ID de una licitación, abrí `licitaciones.json` o usá el dashboard.

**Opciones:**
- `--margen 30` → cambia el margen de ganancia
- `--force`     → recotiza aunque ya tenga precios guardados

### Paso 5 — Ver alertas

```bash
python alertas.py
```

Muestra en consola (con colores) las licitaciones con vencimiento próximo
y envía email si está configurado.

### Paso 6 — Levantar el servidor y dashboard

```bash
python server.py
```

Luego abrí en el navegador: **http://127.0.0.1:5000**

---

## Programar ejecución diaria

### Windows (Task Scheduler)

```powershell
# Crear tarea diaria a las 8:00
schtasks /create /tn "MarcChagall-Alertas" /tr "python C:\ruta\alertas.py" /sc daily /st 08:00
schtasks /create /tn "MarcChagall-Scraper" /tr "python C:\ruta\scraper.py" /sc daily /st 07:00
```

### Linux/Mac (cron)

```bash
# crontab -e
0 7 * * * cd /ruta/marc-chagall-licitaciones && python scraper.py >> logs/cron.log 2>&1
0 8 * * * cd /ruta/marc-chagall-licitaciones && python alertas.py >> logs/cron.log 2>&1
```

---

## Endpoints de la API

| Método | Ruta                | Descripción                        |
|--------|---------------------|------------------------------------|
| GET    | `/licitaciones`     | Lista todas las licitaciones       |
| GET    | `/licitaciones/:id` | Detalle de una licitación          |
| GET    | `/alertas`          | Alertas activas                    |
| GET    | `/stats`            | Estadísticas resumen               |
| GET    | `/config`           | Configuración (sin contraseñas)    |
| POST   | `/config`           | Actualizar configuración           |
| POST   | `/cotizar/:id`      | Generar cotización para una lic.   |
| GET    | `/descargar/:id`    | Descargar Excel de cotización      |
| POST   | `/scrape`           | Ejecutar scraper manualmente       |
| POST   | `/extraer/:id`      | Extraer ítems del PDF              |

---

## Notas

- El scraper respeta `robots.txt` y agrega delay entre requests (configurable en `config.json` con `delay_entre_requests`)
- Si MercadoLibre bloquea el scraping, el sistema cae automáticamente a PrecioClaro
- Los PDFs que no tengan tablas detectables usan extracción por patrones de texto
- Los ítems sin precio quedan marcados para cotización manual en el Excel
