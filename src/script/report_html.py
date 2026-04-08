"""HTML email report generator."""

from __future__ import annotations

import html as html_lib
from datetime import datetime

from .analysis import ProjectAnalyzer
from .models import ProjectSummary, SessionData


class HTMLReporter:
    """Generates a self-contained HTML email body."""

    def __init__(self, analyzer: ProjectAnalyzer):
        self._analyzer = analyzer

    def generate(
        self,
        summaries: list[ProjectSummary],
        projects: dict[str, list[SessionData]],
        comparisons: dict,
        cutoff,
    ) -> str:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        date_range = f"Since {cutoff.strftime('%Y-%m-%d')}" if cutoff else "All time"

        grand_cost = sum(s.total_cost for s in summaries)
        grand_tokens = sum(s.usage.total for s in summaries)
        total_sessions = sum(s.sessions for s in summaries)

        trend_block = self._build_trend_block(comparisons)
        proj_rows = self._build_project_rows(summaries)
        session_rows = self._build_session_rows(projects)

        th = 'style="padding:8px 12px;text-align:left;font-size:11px;color:#7f8c8d;font-weight:600"'
        th_r = 'style="padding:8px 12px;text-align:right;font-size:11px;color:#7f8c8d;font-weight:600"'

        return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="color-scheme" content="light"></head>
<body style="margin:0;padding:20px;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1a1a1a">
<div style="max-width:680px;margin:0 auto">

  <div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);color:white;padding:24px 28px;border-radius:12px;margin-bottom:12px">
    <div style="font-size:20px;font-weight:700">Claude Code &middot; Token Report</div>
    <div style="font-size:13px;opacity:0.6;margin-top:4px">{now_str} &middot; {date_range}</div>
  </div>

  <div style="background:white;border-radius:10px;padding:20px 24px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,0.08)">
    <table style="width:100%;border-collapse:collapse"><tr>
      <td style="text-align:center;padding:4px 12px">
        <div style="font-size:34px;font-weight:800;color:#2c3e50">{self._fmt_cost(grand_cost)}</div>
        <div style="font-size:11px;color:#7f8c8d;text-transform:uppercase;margin-top:2px">Total cost</div>
      </td>
      <td style="text-align:center;padding:4px 12px;border-left:1px solid #f0f0f0;border-right:1px solid #f0f0f0">
        <div style="font-size:34px;font-weight:800;color:#2c3e50">{self._fmt_tokens(grand_tokens)}</div>
        <div style="font-size:11px;color:#7f8c8d;text-transform:uppercase;margin-top:2px">Total tokens</div>
      </td>
      <td style="text-align:center;padding:4px 12px">
        <div style="font-size:34px;font-weight:800;color:#2c3e50">{total_sessions}</div>
        <div style="font-size:11px;color:#7f8c8d;text-transform:uppercase;margin-top:2px">Sessions &middot; {len(summaries)} projects</div>
      </td>
    </tr></table>
  </div>

  {trend_block}

  <div style="background:white;border-radius:10px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,0.08);overflow:hidden">
    <div style="padding:14px 18px 6px;font-size:11px;font-weight:600;color:#7f8c8d;text-transform:uppercase;letter-spacing:0.5px">Top projects</div>
    <table style="width:100%;border-collapse:collapse">
      <thead><tr style="background:#f8f9fa">
        <th {th}>Project</th>
        <th {th_r}>Sessions</th>
        <th {th_r}>Cost</th>
        <th {th_r}>Tokens</th>
        <th {th_r}>Cache hits</th>
      </tr></thead>
      <tbody>{"".join(proj_rows)}</tbody>
    </table>
  </div>

  <div style="background:white;border-radius:10px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,0.08);overflow:hidden">
    <div style="padding:14px 18px 6px;font-size:11px;font-weight:600;color:#7f8c8d;text-transform:uppercase;letter-spacing:0.5px">Top sessions</div>
    <table style="width:100%;border-collapse:collapse">
      <thead><tr style="background:#f8f9fa">
        <th {th}>Date</th>
        <th {th}>Project</th>
        <th {th_r}>Cost</th>
        <th {th}>First prompt</th>
      </tr></thead>
      <tbody>{"".join(session_rows)}</tbody>
    </table>
  </div>

  <div style="text-align:center;font-size:11px;color:#bdc3c7;padding:6px 0">
    Prices scraped live &middot; platform.claude.com/docs/en/about-claude/pricing
  </div>

</div>
</body>
</html>"""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fmt_cost(self, v: float) -> str:
        return f"${v:.2f}"

    def _fmt_tokens(self, v: int) -> str:
        if v >= 1_000_000:
            return f"{v / 1_000_000:.1f}M"
        if v >= 1_000:
            return f"{v / 1_000:.0f}K"
        return str(int(v))

    def _delta_parts(
        self, cost_delta: float, tok_delta: int
    ) -> tuple[str, str, str]:
        sign = "+" if cost_delta >= 0 else ""
        color = (
            "#e67e22" if cost_delta > 0.005
            else ("#27ae60" if cost_delta < -0.005 else "#95a5a6")
        )
        cost_str = f"{sign}${cost_delta:.2f}"
        tok_sign = "+" if tok_delta >= 0 else "-"
        abs_tok = abs(tok_delta)
        if abs_tok >= 1_000_000:
            tok_str = f"{tok_sign}{abs_tok / 1_000_000:.1f}M tok"
        elif abs_tok >= 1_000:
            tok_str = f"{tok_sign}{abs_tok / 1_000:.0f}K tok"
        else:
            tok_str = f"{tok_sign}{int(abs_tok)} tok"
        return cost_str, color, tok_str

    def _build_trend_block(self, comparisons: dict) -> str:
        if not comparisons:
            return ""

        cells = []
        for key, label in [
            ("yesterday", "Yesterday"),
            ("last_week", "Last week"),
            ("last_month", "Last month"),
            ("last_year", "Last year"),
        ]:
            if key in comparisons and comparisons[key] is not None:
                c = comparisons[key]
                cost_str, color, tok_str = self._delta_parts(
                    c["delta_cost"], c["delta_tokens"]
                )
                ref_date = c["run_at"].strftime("%b %d")
                cells.append(
                    f'<td style="padding:0 6px;vertical-align:top">'
                    f'<div style="background:#f8f9fa;border-radius:8px;padding:14px 12px;text-align:center;min-width:115px">'
                    f'<div style="font-size:10px;color:#7f8c8d;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">{label}</div>'
                    f'<div style="font-size:22px;font-weight:700;color:{color}">{cost_str}</div>'
                    f'<div style="font-size:11px;color:#7f8c8d;margin-top:4px">{tok_str}</div>'
                    f'<div style="font-size:9px;color:#bdc3c7;margin-top:6px">ref: {ref_date}</div>'
                    f'</div></td>'
                )
            else:
                cells.append(
                    f'<td style="padding:0 6px;vertical-align:top">'
                    f'<div style="background:#f8f9fa;border-radius:8px;padding:14px 12px;text-align:center;min-width:115px">'
                    f'<div style="font-size:10px;color:#7f8c8d;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">{label}</div>'
                    f'<div style="font-size:22px;font-weight:700;color:#bdc3c7">&#8212;</div>'
                    f'<div style="font-size:11px;color:#bdc3c7;margin-top:4px">no data</div>'
                    f'</div></td>'
                )

        return (
            '<div style="background:white;border-radius:10px;padding:16px 18px;margin-bottom:12px;'
            'box-shadow:0 1px 3px rgba(0,0,0,0.08)">'
            '<div style="font-size:11px;font-weight:600;color:#7f8c8d;text-transform:uppercase;'
            'letter-spacing:0.5px;margin-bottom:12px">Trend (cumulative deltas)</div>'
            '<table style="border-collapse:collapse"><tr>'
            + "".join(cells)
            + "</tr></table></div>"
        )

    def _build_project_rows(self, summaries: list[ProjectSummary]) -> list[str]:
        rows = []
        for s in summaries[:20]:
            u = s.usage
            rows.append(
                "<tr>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:13px">'
                f"{html_lib.escape(s.project)}</td>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:right;font-size:13px;color:#7f8c8d">'
                f"{s.sessions}</td>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:right;font-size:13px;font-weight:600">'
                f"{self._fmt_cost(s.total_cost)}</td>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:right;font-size:13px">'
                f"{self._fmt_tokens(u.total)}</td>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:right;font-size:12px;color:#95a5a6">'
                f"{self._fmt_tokens(u.cache_read_input_tokens)}</td>"
                "</tr>"
            )
        return rows

    def _build_session_rows(
        self, projects: dict[str, list[SessionData]]
    ) -> list[str]:
        rows = []
        for proj, session in self._analyzer.find_costly_sessions(projects, top_n=5):
            ts = session.timestamp_start[:10] if session.timestamp_start else "?"
            first_prompt = (
                html_lib.escape(session.prompts[0].text[:120].replace("\n", " "))
                if session.prompts else ""
            )
            rows.append(
                "<tr>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#7f8c8d;white-space:nowrap">'
                f"{ts}</td>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:12px">'
                f"{html_lib.escape(proj)}</td>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:right;font-size:12px;font-weight:600;white-space:nowrap">'
                f"{self._fmt_cost(session.cost)}</td>"
                f'<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#7f8c8d">'
                f"{first_prompt}</td>"
                "</tr>"
            )
        return rows
