# token-cost-analysis

Script Python para analizar uso de tokens de Claude Code y enviar reporte diario por Slack (y opcionalmente por mail).

Lee los JSONL de `~/.claude/projects/`, calcula costos con precios scrapeados en vivo de Anthropic (via Playwright), y genera:

- Reporte Markdown en `~/tuin/analysis/tokens/token_report.md`
- Prompts por proyecto en `~/tuin/analysis/tokens/prompts/`
- Historial en SQLite (`database/claude.db`)
- Mensaje a Slack (Block Kit, en español) con resumen, tendencias y top proyectos/sesiones
- Email HTML opcional via Mail.app/osascript

## Uso

```bash
cd ~/.claude/token-cost-analysis && .venv/bin/python3 -m src.script.main

# Con filtro de fecha:
cd ~/.claude/token-cost-analysis && SINCE_DATE=2026-03-30 .venv/bin/python3 -m src.script.main
cd ~/.claude/token-cost-analysis && SINCE_DAYS=7 .venv/bin/python3 -m src.script.main
```

Siempre usar `.venv/bin/python3` — Python instalado via **pyenv**, no brew. Correr desde la raíz del proyecto.

### Backfill

Reprocessa todas las sesiones JSONL en disco y carga el historial día por día en la DB (saltea fechas que ya existen):

```bash
cd ~/.claude/token-cost-analysis && .venv/bin/python3 -m src.script.backfill

# Desde una fecha específica:
BACKFILL_SINCE_DATE=2026-04-01 .venv/bin/python3 -m src.script.backfill
```

## Variables de entorno

Configurar en `.env` en la raíz del proyecto. `cron.sh` las carga automáticamente con `set -a` / `source .env`.

| Variable            | Default  | Descripción                                           |
|---------------------|----------|-------------------------------------------------------|
| `SINCE_DATE`        | (vacío)  | Filtrar sesiones desde esta fecha (ISO). Precedencia. |
| `SINCE_DAYS`        | `1`      | Filtrar sesiones de los últimos N días                |
| `SLACK_ENABLED`     | `true`   | Enviar reporte a Slack                                |
| `SLACK_BOT_TOKEN`   | (vacío)  | Bot token de Slack                                    |
| `SLACK_CHANNEL_ID`  | (vacío)  | Canal o user ID de Slack destino                      |
| `EMAIL_ENABLED`     | `false`  | Enviar reporte por email via Mail.app                 |
| `EMAIL_RECIPIENT`   | (vacío)  | Dirección de email destino                            |
| `PROJECT_STRIP_PREFIX` | (vacío) | Prefijo a remover de los nombres de proyectos y sesiones (e.g. `workspace-gitlab-myorg-`) |
| `PROJECT_INCLUDE_PREFIX` | (vacío) | Solo mostrar proyectos cuyo nombre (sin el prefijo OS) empiece con este string. Vacío = sin filtro. Los proyectos excluidos igual se guardan en la DB. |
| `OPEN_BROWSER`      | `false`  | Al finalizar, abre un dashboard HTML en Google Chrome con todos los proyectos (sin filtro de prefijo). Útil para correr manualmente. |

`SINCE_DATE` tiene precedencia sobre `SINCE_DAYS`.

## Cron

Para ejecutar el reporte automáticamente cada día, agregar una entrada al crontab del sistema (`crontab -e`).

El cron usa **hora local del sistema**. Ajustar la hora según tu timezone:

```
# 9:00 AM hora local
0 9 * * * /path/to/token-cost-analysis/cron.sh >> /path/to/token-cost-analysis/daily.log 2>&1
```

Reemplazar `/path/to/token-cost-analysis` con la ruta absoluta donde clonaste el repo (e.g. `~/.claude/token-cost-analysis`). Para agregar vía terminal:

```bash
(crontab -l 2>/dev/null; echo "0 9 * * * $(pwd)/cron.sh >> $(pwd)/daily.log 2>&1") | crontab -
```

`cron.sh` carga el `.env` automáticamente, invoca el script Python y, si falla, envía un email de error via Mail.app.

## Dependencias

- **playwright** (chromium) — para scrapear precios de `platform.claude.com/docs/en/about-claude/pricing`
- **python-dotenv** — carga el `.env` automáticamente al correr el script directamente (sin necesidad de `source .env`)
- Stdlib only para el resto (`csv`, `json`, `sqlite3`, `urllib`, `subprocess`, `zoneinfo`)

Instalar:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

## Notas

- Los precios se scrapean al inicio de cada ejecución. Si la página de Anthropic cambia de estructura, hay que actualizar `DISPLAY_NAME_TO_MODEL_PREFIX` y/o el regex en `src/script/pricing.py`.
- El historial en SQLite reemplaza la entrada del día actual si se ejecuta más de una vez por día (idempotente). Los CSVs legacy se migran automáticamente en la primera ejecución.

## Estado conocido

- **Email no funciona bien** — el envío via Mail.app/osascript tiene problemas y necesita ser arreglado. Por ahora usar solo Slack (`EMAIL_ENABLED=false`).
