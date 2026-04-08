"""Slack notifier: builds Block Kit report and posts via API."""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime

from .models import ProjectSummary, SessionData
from .pricing import PricingService, PRICING_PAGE_URL

_MESES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _shorten_project(name: str) -> str:
    """Return the most specific segment of a GitLab-style project path."""
    parts = [p for p in name.split("---") if p]
    return parts[-1] if parts else name


def _shorten_model(m: str) -> str:
    m = re.sub(r"claude-(\w+)-([\d]+)-([\d]+).*", r"\1-\2.\3", m)
    m = re.sub(r"claude-(\w+)-([\d]+).*", r"\1-\2", m)
    return m


class SlackNotifier:
    """Posts a token usage report to Slack using Block Kit (in Spanish)."""

    def __init__(self, token: str, channel_id: str, pricing: PricingService, tz_local):
        self._token = token
        self._channel_id = channel_id
        self._pricing = pricing
        self._tz_local = tz_local

    def send(
        self,
        summaries: list[ProjectSummary],
        projects: dict[str, list[SessionData]],
        comparisons: dict,
        project_comparisons: dict,
        cache_hit_pct: float,
        cutoff,
    ) -> None:
        blocks = self._build_blocks(
            summaries, projects, comparisons, project_comparisons, cache_hit_pct, cutoff
        )

        target = self._channel_id
        if self._channel_id.startswith("U"):
            dm = self._post("conversations.open", {"users": self._channel_id})
            if not dm.get("ok"):
                raise RuntimeError(f"Slack conversations.open failed: {dm.get('error')}")
            target = dm["channel"]["id"]

        body = self._post("chat.postMessage", {"channel": target, "blocks": blocks})
        if not body.get("ok"):
            raise RuntimeError(f"Slack API error: {body.get('error')}")

        print(f"Slack message sent to {self._channel_id}")

    # ------------------------------------------------------------------
    # Block Kit builder helpers
    # ------------------------------------------------------------------

    def _build_blocks(
        self,
        summaries: list[ProjectSummary],
        projects: dict[str, list[SessionData]],
        comparisons: dict,
        project_comparisons: dict,
        cache_hit_pct: float,
        cutoff,
    ) -> list[dict]:
        now = datetime.now(self._tz_local)
        fecha = f"{now.day} de {_MESES_ES[now.month]} de {now.year}"
        cutoff_local = cutoff.astimezone(self._tz_local) if cutoff else None
        desde = (
            f"{cutoff_local.day} de {_MESES_ES[cutoff_local.month]}"
            if cutoff_local else "siempre"
        )

        grand_cost = sum(s.total_cost for s in summaries)
        grand_tokens = sum(s.usage.total for s in summaries)
        total_sessions = sum(s.sessions for s in summaries)

        trend_section = self._build_trend_section(comparisons, cache_hit_pct)
        proj_blocks = self._build_project_blocks(summaries, project_comparisons)
        session_blocks = self._build_session_blocks(projects)
        pricing_footnote = self._build_pricing_footnote()

        blocks: list[dict] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Reporte de tokens   ·   {fecha}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Período:* {desde}\n"
                        f"*Costo estimado:* {self._fmt_cost(grand_cost)}   ·   "
                        f"*Tokens:* {self._fmt_tokens(grand_tokens)}   ·   "
                        f"*Caché hit:* {cache_hit_pct:.0f}%   ·   "
                        f"*Sesiones:* {total_sessions:,}"
                    ),
                },
            },
            {"type": "divider"},
            trend_section,
        ]

        blocks += [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Proyectos más costosos*"}},
        ] + proj_blocks

        if session_blocks:
            blocks += [
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "*Sesiones más costosas*"}},
            ] + session_blocks

        blocks += [
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": pricing_footnote}]},
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        f"Los costos son estimaciones basadas en precios públicos de "
                        f"<{PRICING_PAGE_URL}|anthropic.com/pricing>. "
                        "El costo real puede diferir según el plan contratado (ej: Enterprise)."
                    ),
                }],
            },
        ]

        return blocks

    def _build_trend_section(self, comparisons: dict, current_cache_hit_pct: float) -> dict:
        periods = [
            ("yesterday", "1d"),
            ("last_3_days", "3d"),
            ("last_week", "7d"),
            ("last_month", "30d"),
            ("last_year", "365d"),
        ]
        lines = []

        for key, label in periods:
            if key in comparisons:
                c = comparisons[key]
                if c is None:
                    lines.append(f"*{label}:* _sin datos históricos_")
                    continue
                delta = c["delta_cost"]
                cost_sign = "+" if delta >= 0 else ""
                tok_str = self._fmt_tok_delta(c["delta_tokens"])
                pct = c.get("pct", 0)
                pct_sign = "+" if pct >= 0 else ""
                ref_chit = c.get("cache_hit_pct", 0)
                delta_chit = current_cache_hit_pct - ref_chit
                chit_sign = "+" if delta_chit >= 0 else ""
                lines.append(
                    f"*{label}:* {cost_sign}${delta:.2f} USD   ·   {tok_str}   ·   "
                    f"{pct_sign}{pct:.0f}%   ·   {chit_sign}{delta_chit:.0f}% caché hit"
                )

        trend_text = "*Tendencia*\n" + "\n".join(lines) if lines else "*Tendencia*  _sin datos históricos_"

        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": trend_text},
        }

    def _build_project_blocks(
        self,
        summaries: list[ProjectSummary],
        project_comparisons: dict,
    ) -> list[dict]:
        blocks = []
        for i, s in enumerate(summaries[:10], 1):
            u = s.usage
            proj_hit = (
                u.cache_read_input_tokens / u.total_input * 100
                if u.total_input > 0 else 0.0
            )
            sesiones_label = "sesión" if s.sessions == 1 else "sesiones"
            tok_total = self._fmt_tokens(s.usage.total)

            proj_comp = project_comparisons.get(s.project, {})
            delta_lines = []
            for key, period in (("yesterday", "1d"), ("last_week", "7d"), ("last_month", "30d")):
                if key in proj_comp:
                    c = proj_comp[key]
                    if c is None:
                        delta_lines.append(f"Delta {period}: _sin datos históricos_")
                        continue
                    rc = round(c["delta_cost"], 2)
                    rp = round(c["pct"])
                    rh = round(c["delta_hit"])
                    cost_str = (
                        f"+${rc:.2f}" if rc > 0 else (f"-${abs(rc):.2f}" if rc < 0 else "$0.00")
                    )
                    pct_str = f"+{rp}%" if rp > 0 else (f"{rp}%" if rp < 0 else "0%")
                    hit_str = (
                        f"+{rh}% caché hit" if rh > 0
                        else (f"{rh}% caché hit" if rh < 0 else "0% caché hit")
                    )
                    delta_lines.append(
                        f"Delta {period}: {cost_str} USD   ·   {self._fmt_tok_delta(c['delta_tokens'])}"
                        f"   ·   {pct_str}   ·   {hit_str}"
                    )

            context_text = (
                ("\n".join(delta_lines) + "\n") if delta_lines else "_sin datos históricos_\n"
            )
            context_text += f"{s.sessions} {sesiones_label}"

            blocks += [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{i}. {s.project}*\n"
                            f"Costo estimado: {self._fmt_cost(s.total_cost)}   ·   Tokens: {tok_total}   ·   Caché hit: {proj_hit:.0f}%"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": context_text}],
                },
            ]
        return blocks

    def _build_session_blocks(
        self, projects: dict[str, list[SessionData]]
    ) -> list[dict]:
        top_sessions = sorted(
            ((p, s) for p, sessions in projects.items() for s in sessions),
            key=lambda x: x[1].cost,
            reverse=True,
        )[:5]

        blocks = []
        for i, (proj, session) in enumerate(top_sessions, 1):
            first_raw = session.prompts[0].text if session.prompts else ""
            first = (
                (first_raw[:300].replace("\n", " ") + "…")
                if len(first_raw) > 300
                else first_raw.replace("\n", " ")
            )
            models_short = [
                _shorten_model(m)
                for m in (session.models or ([session.model] if session.model else []))
            ]
            u = session.usage
            total_tokens = u.total
            cache_hit = (
                u.cache_read_input_tokens / u.total_input * 100
                if u.total_input > 0 else 0
            )
            n_prompts = len(session.prompts)
            prompts_label = "prompt" if n_prompts == 1 else "prompts"

            context_lines = []
            if first:
                context_lines.append(first)
            if models_short:
                context_lines.append("   ·   ".join(f"`{m}`" for m in models_short))
            context_lines.append(f"{n_prompts} {prompts_label}")

            blocks += [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{i}. {proj}*\n"
                            f"Costo estimado: {self._fmt_cost(session.cost)}   ·   Tokens: {self._fmt_tokens(total_tokens)}   ·   Caché hit: {cache_hit:.0f}%"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "\n".join(context_lines)}],
                },
            ]
        return blocks

    def _build_pricing_footnote(self) -> str:
        price_order = [
            ("output", "Output", "El más caro. Cada token que Claude genera en su respuesta."),
            ("cache_write", "Cache creation", "Guardar contexto en caché para reutilizarlo. Se paga una vez."),
            ("input", "Input", "Tokens que enviás en cada mensaje (prompt + historial)."),
            ("cache_read", "Cache read", "Leer contexto ya cacheado. ~10x más barato que input — lo ideal."),
        ]
        sample = next(iter(self._pricing.pricing.values()), None)
        lines = ["Tipos de tokens (de más caro a más barato)"]
        for key, label, desc in price_order:
            price = getattr(sample, key, 0) if sample else 0
            lines.append(f"• *{label}* (${price}/MTok) — {desc}")
        lines.append(
            "_% de caché hit: proporción del input leído desde caché. "
            "Cuanto más alto, más eficiente la sesión._"
        )
        return "\n".join(lines)

    def _fmt_cost(self, v: float) -> str:
        return f"${v:.2f}"

    def _fmt_tokens(self, v: int) -> str:
        if v >= 1_000_000:
            return f"{v / 1_000_000:.1f}M"
        if v >= 1_000:
            return f"{v / 1_000:.0f}K"
        return str(int(v))

    def _fmt_tok_delta(self, tok_delta: int) -> str:
        sign = "+" if tok_delta >= 0 else "-"
        abs_tok = abs(tok_delta)
        if abs_tok >= 1_000_000:
            return f"{sign}{abs_tok / 1_000_000:.1f}M tok"
        if abs_tok >= 1_000:
            return f"{sign}{abs_tok / 1_000:.0f}K tok"
        return f"{sign}{int(abs_tok)} tok"

    def _post(self, endpoint: str, data: dict) -> dict:
        req = urllib.request.Request(
            f"https://slack.com/api/{endpoint}",
            data=json.dumps(data).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
