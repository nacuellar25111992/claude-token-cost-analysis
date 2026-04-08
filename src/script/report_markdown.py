"""Markdown report writer and stdout summary printer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .analysis import ProjectAnalyzer
from .config import Config
from .models import ProjectSummary, SessionData
from .pricing import PricingService


def _fmt_tokens(n: int) -> str:
    return f"{n:,}"


def _fmt_cost(usd: float) -> str:
    return f"${usd:.2f}"


class MarkdownReporter:
    """Writes the token_report.md file and per-project prompt files."""

    def __init__(self, config: Config, analyzer: ProjectAnalyzer, pricing: PricingService):
        self._config = config
        self._analyzer = analyzer
        self._pricing = pricing

    def write_report(
        self,
        projects: dict[str, list[SessionData]],
        summaries: list[ProjectSummary],
    ) -> Path:
        output_dir = self._config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "token_report.md"

        cutoff = self._config.cutoff
        date_range = f"Since {cutoff.strftime('%Y-%m-%d')}" if cutoff else "All time"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "# Claude Code Token Usage Analysis",
            f"\nGenerated: {now} | Range: {date_range}\n",
        ]

        grand_input = sum(s.usage.input_tokens for s in summaries)
        grand_cache_create = sum(s.usage.cache_creation_input_tokens for s in summaries)
        grand_cache_read = sum(s.usage.cache_read_input_tokens for s in summaries)
        grand_output = sum(s.usage.output_tokens for s in summaries)
        grand_total = sum(s.usage.total for s in summaries)
        grand_cost = sum(s.total_cost for s in summaries)
        total_sessions = sum(s.sessions for s in summaries)
        total_subagent_tokens = sum(s.subagent_tokens for s in summaries)
        total_subagent_count = sum(s.subagent_count for s in summaries)

        lines += [
            "## Grand Totals\n",
            f"- **Projects**: {len(summaries)}",
            f"- **Sessions**: {total_sessions:,}",
            f"- **Estimated cost**: {_fmt_cost(grand_cost)}",
            f"- **Total tokens**: {_fmt_tokens(grand_total)}",
            f"  - Input: {_fmt_tokens(grand_input)}",
            f"  - Cache creation: {_fmt_tokens(grand_cache_create)}",
            f"  - Cache read: {_fmt_tokens(grand_cache_read)}",
            f"  - Output: {_fmt_tokens(grand_output)}",
            f"- **Subagent sessions**: {total_subagent_count:,} ({_fmt_tokens(total_subagent_tokens)} tokens)",
        ]

        price_note = " | ".join(
            f"{k}: ${v.input}/{v.output}"
            for k, v in self._pricing.pricing.items()
        )
        lines.append(
            f"\n> Prices scraped live from anthropic.com/pricing."
            f" {price_note} (per 1M input/output tokens).\n"
        )

        lines += [
            "## By Project\n",
            "| Project | Sessions | Cost | Total Tokens | Input | Cache Create | Cache Read | Output | Subagents |",
            "|---------|----------|------|--------------|-------|--------------|------------|--------|-----------|",
        ]
        for s in summaries:
            u = s.usage
            lines.append(
                f"| {s.project} | {s.sessions} "
                f"| {_fmt_cost(s.total_cost)} "
                f"| {_fmt_tokens(u.total)} "
                f"| {_fmt_tokens(u.input_tokens)} "
                f"| {_fmt_tokens(u.cache_creation_input_tokens)} "
                f"| {_fmt_tokens(u.cache_read_input_tokens)} "
                f"| {_fmt_tokens(u.output_tokens)} "
                f"| {s.subagent_count} ({_fmt_tokens(s.subagent_tokens)}) |"
            )
        lines.append("")

        lines.append("## Most Costly Sessions\n")
        for i, (proj, session) in enumerate(
            self._analyzer.find_costly_sessions(projects, top_n=25), 1
        ):
            u = session.usage
            lines.append(
                f"### {i}. {proj} — {_fmt_tokens(session.usage.total)} tokens"
                f" ({_fmt_cost(session.cost)})"
            )
            lines.append(f"- **Session**: `{session.session_id}`")
            if session.timestamp_start:
                lines.append(
                    f"- **Started**: {session.timestamp_start[:19].replace('T', ' ')}"
                )
            if session.model:
                lines.append(f"- **Model**: `{session.model}`")
            lines.append(
                f"- **Tokens**: input={_fmt_tokens(u.input_tokens)}, "
                f"cache_create={_fmt_tokens(u.cache_creation_input_tokens)}, "
                f"cache_read={_fmt_tokens(u.cache_read_input_tokens)}, "
                f"output={_fmt_tokens(u.output_tokens)}"
            )
            lines.append(f"- **Subagents in session**: {len(session.subagent_sessions)}")
            if session.prompts:
                first = session.prompts[0].text[:400].replace("\n", " ")
                lines.append("- **First prompt**:")
                lines.append(f"  > {first}")
            lines.append("")

        lines += [
            "## Most Costly Subagents\n",
            "| # | Project | Parent Session | Subagent File | Cost | Total Tokens |"
            " Input | Cache Create | Cache Read | Output |",
            "|---|---------|----------------|---------------|------|--------------|"
            "-------|--------------|------------|--------|",
        ]
        for i, (proj, session_id, sub) in enumerate(
            self._analyzer.find_costly_subagents(projects, top_n=20), 1
        ):
            u = sub.usage
            sub_file = Path(sub.file).name
            lines.append(
                f"| {i} | {proj} | `{session_id[:8]}...` "
                f"| `{sub_file}` "
                f"| {_fmt_cost(sub.cost)} "
                f"| {_fmt_tokens(sub.usage.total)} "
                f"| {_fmt_tokens(u.input_tokens)} "
                f"| {_fmt_tokens(u.cache_creation_input_tokens)} "
                f"| {_fmt_tokens(u.cache_read_input_tokens)} "
                f"| {_fmt_tokens(u.output_tokens)} |"
            )
        lines.append("")

        lines.append("## Subagent Usage by Project\n")
        proj_sub_stats = []
        for proj_name, sessions in projects.items():
            sub_tokens = sum(
                sub.usage.total for s in sessions for sub in s.subagent_sessions
            )
            sub_count = sum(len(s.subagent_sessions) for s in sessions)
            if sub_count > 0:
                proj_sub_stats.append((proj_name, sub_count, sub_tokens))
        proj_sub_stats.sort(key=lambda x: x[2], reverse=True)
        lines += [
            "| Project | Subagent Sessions | Subagent Tokens |",
            "|---------|-------------------|-----------------|",
        ]
        for proj_name, count, tokens in proj_sub_stats:
            lines.append(f"| {proj_name} | {count} | {_fmt_tokens(tokens)} |")
        lines.append("")

        with open(report_path, "w") as f:
            f.write("\n".join(lines))

        print(f"Report written: {report_path}")
        return report_path

    def write_prompts(self, projects: dict[str, list[SessionData]]) -> None:
        """Write all user prompts for each project to separate markdown files."""
        prompts_dir = self._config.output_dir / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)

        for project_name, sessions in projects.items():
            all_prompts = [
                {**vars(p), "session_id": session.session_id}
                for session in sessions
                for p in session.prompts
            ]
            if not all_prompts:
                continue

            all_prompts.sort(key=lambda x: x["timestamp"] or "")
            safe_name = project_name.replace("/", "_").replace(" ", "_")[:80]
            out_path = prompts_dir / f"{safe_name}.md"

            lines = [
                f"# Prompts: {project_name}",
                f"\n{len(all_prompts)} prompts across {len(sessions)} sessions\n",
            ]
            for i, p in enumerate(all_prompts, 1):
                ts = p["timestamp"][:19].replace("T", " ") if p["timestamp"] else "unknown"
                lines.append(f"## {i}. [{ts}] Session `{p['session_id'][:8]}`")
                if p["entrypoint"]:
                    lines.append(f"*entrypoint: {p['entrypoint']}*")
                lines.append("")
                lines.append(p["text"])
                lines.append("")

            with open(out_path, "w") as f:
                f.write("\n".join(lines))

        print(f"Prompt files written to: {prompts_dir}")

    def print_summary(
        self,
        summaries: list[ProjectSummary],
        projects: dict[str, list[SessionData]],
    ) -> None:
        """Print a quick summary table to stdout."""
        grand_total = sum(s.usage.total for s in summaries)
        grand_cost = sum(s.total_cost for s in summaries)
        total_sessions = sum(s.sessions for s in summaries)

        print(
            f"\nTotal: {_fmt_tokens(grand_total)} tokens"
            f" | Estimated cost: {_fmt_cost(grand_cost)}"
        )
        print(f"Across {total_sessions} sessions in {len(summaries)} projects\n")
        print(
            f"{'Project':<50} {'Sessions':>8} {'Cost':>10}"
            f" {'Total Tokens':>14} {'Subagents':>10}"
        )
        print("-" * 96)

        for s in summaries[:30]:
            print(
                f"{s.project:<50} {s.sessions:>8,} "
                f"{_fmt_cost(s.total_cost):>10} "
                f"{_fmt_tokens(s.usage.total):>14} "
                f"{s.subagent_count:>10,}"
            )

        print("\nTop 10 costliest sessions:")
        for proj, session in self._analyzer.find_costly_sessions(projects, top_n=10):
            ts = session.timestamp_start[:10] if session.timestamp_start else "?"
            first_prompt = (
                session.prompts[0].text[:80].replace("\n", " ")
                if session.prompts else ""
            )
            print(
                f"  [{ts}] {proj}: {_fmt_tokens(session.usage.total)}"
                f" ({_fmt_cost(session.cost)}) — {first_prompt}"
            )
