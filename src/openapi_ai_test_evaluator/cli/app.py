"""OATE command-line application."""

import json
from pathlib import Path
from typing import Annotated

import typer

from openapi_ai_test_evaluator.domain import TestPlan
from openapi_ai_test_evaluator.validation import PlanLoadError, load_test_plan

app = typer.Typer(
    name="oate",
    help="Generate and evaluate declarative tests for OpenAPI services.",
    no_args_is_help=True,
)
plan_app = typer.Typer(help="Inspect and validate TestPlan documents.", no_args_is_help=True)
app.add_typer(plan_app, name="plan")


@plan_app.command("validate")
def validate_plan(
    plan: Annotated[
        Path,
        typer.Option("--plan", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    """Structurally validate a TestPlan YAML document."""
    try:
        test_plan = load_test_plan(plan)
    except PlanLoadError as error:
        if json_output:
            typer.echo(json.dumps({"valid": False, "path": str(plan), "error": str(error)}))
        else:
            typer.echo(f"Invalid TestPlan: {plan}\n{error}", err=True)
        raise typer.Exit(code=1) from error

    result = {
        "valid": True,
        "path": str(plan),
        "scenarios": len(test_plan.scenarios),
        "steps": sum(
            len(scenario.setup) + len(scenario.steps) + len(scenario.cleanup)
            for scenario in test_plan.scenarios
        ),
        "relations": sum(len(scenario.relations) for scenario in test_plan.scenarios),
    }
    if json_output:
        typer.echo(json.dumps(result))
    else:
        typer.echo(
            "Valid TestPlan: "
            f"{result['scenarios']} scenarios, {result['steps']} steps, "
            f"{result['relations']} relations"
        )


@plan_app.command("schema")
def export_plan_schema(
    out: Annotated[
        Path,
        typer.Option("--out", file_okay=True, dir_okay=False),
    ] = Path("schemas/test-plan.schema.json"),
) -> None:
    """Export the JSON Schema generated from the Pydantic contract."""
    out.parent.mkdir(parents=True, exist_ok=True)
    schema = TestPlan.model_json_schema()
    out.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Wrote TestPlan schema: {out}")
