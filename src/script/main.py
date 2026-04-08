"""Main entry point: wire up all components and run the analysis."""

from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .analysis import ProjectAnalyzer
from .config import Config
from .history import HistoryStore
from .notifier_email import EmailNotifier
from .notifier_slack import SlackNotifier
from .parser import SessionParser
from .pricing import PricingService
from .report_html import HTMLReporter
from .report_markdown import MarkdownReporter


def main() -> None:
    config = Config.from_env()

    # 1. Load pricing (DB cache or live scrape)
    db_path = config.database_dir / "claude.db"
    pricing = PricingService(db_path)
    pricing.load()

    # 2. Parse and analyze all sessions
    parser = SessionParser(pricing)
    analyzer = ProjectAnalyzer(config, parser)

    print("Scanning projects...")
    all_projects, display_projects = analyzer.analyze()

    # all_summaries uses intermediate names → stored in CSV for full history
    # display_summaries uses display names → used for output, Slack, email
    all_summaries = analyzer.summarize(all_projects)
    display_summaries = analyzer.summarize(display_projects)

    skipped = len(all_summaries) - len(display_summaries)
    filter_note = (
        f" (filter: '{config.project_include_prefix}', {skipped} projects excluded)"
        if config.project_include_prefix else ""
    )
    print(f"Found {len(all_summaries)} projects{filter_note}")

    # 3. Load history comparisons BEFORE writing the current run
    history = HistoryStore(db_path, config.project_strip_prefix)

    if history.count_runs() <= 1:
        print(
            "\nHint: only one (or zero) historical runs found in the DB.\n"
            "Run the backfill script to load the full history:\n"
            "  .venv/bin/python3 -m src.script.backfill\n"
        )

    all_grand_cost = sum(s.total_cost for s in all_summaries)
    all_grand_tokens = sum(s.usage.total for s in all_summaries)
    comparisons = history.load_comparisons(all_grand_cost, all_grand_tokens)
    project_comparisons = history.load_project_comparisons(display_summaries)

    # 4. Compute cache hit % for display summaries
    grand_cache_read = sum(s.usage.cache_read_input_tokens for s in display_summaries)
    grand_input_all = sum(s.usage.total_input for s in display_summaries)
    grand_cache_hit_pct = (grand_cache_read / grand_input_all * 100) if grand_input_all > 0 else 0.0

    # 5. Write reports
    reporter = MarkdownReporter(config, analyzer, pricing)
    reporter.print_summary(display_summaries, display_projects)
    report_path = reporter.write_report(display_projects, display_summaries)
    reporter.write_prompts(display_projects)

    # 6. Persist history (ALL projects) and pricing
    history.write_sessions(all_projects)
    history.write_runs(all_summaries, cutoff=config.cutoff)
    pricing.save()

    # 7. Send notifications
    html_reporter = HTMLReporter(analyzer)
    html_body = html_reporter.generate(
        display_summaries, display_projects, comparisons, config.cutoff
    )

    if config.email_enabled and config.email_recipient:
        subject = f"Claude Code Token Report — {datetime.now().strftime('%Y-%m-%d')}"
        EmailNotifier(config.email_recipient).send(subject, html_body)

    if config.slack_enabled and config.slack_bot_token and config.slack_channel_id:
        SlackNotifier(
            config.slack_bot_token,
            config.slack_channel_id,
            pricing,
            config.tz_local,
        ).send(
            display_summaries,
            display_projects,
            comparisons,
            project_comparisons,
            grand_cache_hit_pct,
            config.cutoff,
        )

    if config.open_browser:
        # Browser dashboard shows all projects (ignores include-prefix filter)
        all_comparisons = history.load_comparisons(all_grand_cost, all_grand_tokens)
        browser_html = html_reporter.generate(
            all_summaries, all_projects, all_comparisons, config.cutoff
        )
        html_path = Path(tempfile.gettempdir()) / f"claude_token_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        html_path.write_text(browser_html, encoding="utf-8")
        subprocess.run(["open", "-a", "Google Chrome", str(html_path)])
        subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'])
        print(f"Dashboard: {html_path}")

    print(f"\nFull report: {report_path}")
    print(f"Prompts: {config.output_dir}/prompts/")


if __name__ == "__main__":
    main()
