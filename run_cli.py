"""
Rootly CLI.

    python run_cli.py                     # list available scenarios
    python run_cli.py ALRT-001            # run the diagnosis, compact live trace
    python run_cli.py ALRT-001 --verbose  # also show full tool observations
    python run_cli.py ALRT-001 --export   # save diagnosis .json/.md and trace .json to exports/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from src.agent import DiagnosisError, run_diagnosis
from src.agent.report import diagnosis_dict, to_markdown
from src.data_loader import get_alert, load_alerts

EXPORT_DIR = Path(__file__).resolve().parent / "exports"
console = Console()


def list_scenarios() -> None:
    table = Table(title="Available alert scenarios")
    for column in ("ID", "Service", "Type", "Severity", "Description"):
        table.add_column(column)
    for alert in load_alerts():
        table.add_row(alert.id, alert.service, alert.alert_type.value, alert.severity_reported.value, alert.description)
    console.print(table)
    console.print("Run one with: [bold]python run_cli.py ALRT-001[/bold]")


def _observation_summary(result: dict) -> str:
    if "error" in result:
        return f"[red]error:[/red] {result['error']}"
    if "component" in result:
        c = result["component"]
        return f"{c['id']} {c['name']} ({c['type']}, {c['criticality']}) depends on: {', '.join(c['depends_on']) or '—'}"
    if "logs" in result:
        errors = sum(1 for entry in result["logs"] if entry["level"] in ("ERROR", "FATAL"))
        return f"{result['total_matches']} log entries ({errors} ERROR/FATAL)"
    if "results" in result:
        return ", ".join(f"{r['id']} ({r['similarity']:.2f})" for r in result["results"])
    return json.dumps(result)[:160]


def make_printer(verbose: bool):
    def on_event(event: dict) -> None:
        step, kind = event["step"], event["kind"]
        prefix = f"[dim][Step {step}][/dim]"
        if kind == "thought":
            console.print(f"{prefix} 💭 [cyan]Thought:[/cyan] {event['content']}")
        elif kind == "action":
            args = ", ".join(f"{k}={v!r}" for k, v in event["input"].items())
            console.print(f"{prefix} 🔧 [yellow]Action:[/yellow] {event['tool']}({args})")
        elif kind == "observation":
            console.print(f"{prefix} 📋 [green]Observation:[/green] {_observation_summary(event['result'])} "
                          f"[dim]({event['duration_ms']:.0f} ms)[/dim]")
            if verbose:
                console.print_json(data=event["result"])
        elif kind == "guardrail":
            style = "red" if event.get("is_error") else "green"
            console.print(f"{prefix} 🛡️  [{style}]Guardrail:[/{style}] {event['content']}")
        elif kind == "note":
            console.print(f"{prefix} ⚠️  [magenta]{event['content']}[/magenta]")

    return on_event


def run(alert_id: str, verbose: bool, export: bool) -> int:
    try:
        alert = get_alert(alert_id)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/red]")
        return 2

    console.print(Panel.fit("[bold]ROOTLY — Incident Diagnosis Agent[/bold]", border_style="blue"))
    console.print(f"[bold]Alert:[/bold] {alert.id} | {alert.service} | {alert.alert_type.value} | "
                  f"{alert.severity_reported.value.upper()}\n{alert.description}\n")
    console.rule("Agent Trace")

    try:
        result = run_diagnosis(alert, on_event=make_printer(verbose))
    except DiagnosisError as exc:
        console.print(f"[red]Diagnosis failed:[/red] {exc}")
        return 1

    console.rule("Diagnosis Package")
    console.print(Markdown(to_markdown(result)))

    if export:
        EXPORT_DIR.mkdir(exist_ok=True)
        base = EXPORT_DIR / alert.id
        base.with_name(f"{alert.id}_diagnosis.json").write_text(json.dumps(diagnosis_dict(result), indent=2, ensure_ascii=False), encoding="utf-8")
        base.with_name(f"{alert.id}_diagnosis.md").write_text(to_markdown(result), encoding="utf-8")
        base.with_name(f"{alert.id}_trace.json").write_text(json.dumps(result.trace, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"\n[green]Exported to {EXPORT_DIR}[/green]")
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Rootly — AI incident diagnosis agent")
    parser.add_argument("alert_id", nargs="?", help="Alert scenario to diagnose, e.g. ALRT-001")
    parser.add_argument("--verbose", action="store_true", help="Show full tool observations")
    parser.add_argument("--export", action="store_true", help="Save diagnosis (.json, .md) and trace (.json) to exports/")
    args = parser.parse_args()

    if not args.alert_id:
        list_scenarios()
        return 0
    return run(args.alert_id, args.verbose, args.export)


if __name__ == "__main__":
    sys.exit(main())
