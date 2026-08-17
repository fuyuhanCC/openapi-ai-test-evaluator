"""OATE command-line application."""

import json
from pathlib import Path
from typing import Annotated

import typer

from openapi_ai_test_evaluator.domain import TestPlan
from openapi_ai_test_evaluator.spec import SpecLoadError, load_openapi
from openapi_ai_test_evaluator.validation import (
    PlanLoadError,
    load_test_plan,
    validate_plan_semantics,
)

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
    spec: Annotated[
        Path | None,
        typer.Option(
            "--spec",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Also validate the plan against an OpenAPI 3.0 document.",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    """Validate a TestPlan, optionally including OpenAPI semantics."""
    try:
        test_plan = load_test_plan(plan)
    except PlanLoadError as error:
        if json_output:
            typer.echo(json.dumps({"valid": False, "path": str(plan), "error": str(error)}))
        else:
            typer.echo(f"Invalid TestPlan: {plan}\n{error}", err=True)
        raise typer.Exit(code=1) from error

    if spec is not None:
        try:
            openapi_spec = load_openapi(spec)
        except SpecLoadError as error:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "valid": False,
                            "stage": "openapi",
                            "path": str(spec),
                            "error": str(error),
                        }
                    )
                )
            else:
                typer.echo(f"Invalid OpenAPI document: {spec}\n{error}", err=True)
            raise typer.Exit(code=1) from error

        semantic_issues = validate_plan_semantics(test_plan, openapi_spec)
        if semantic_issues:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "valid": False,
                            "stage": "semantic",
                            "plan": str(plan),
                            "spec": str(spec),
                            "issues": [issue.model_dump() for issue in semantic_issues],
                        }
                    )
                )
            else:
                typer.echo(f"Invalid TestPlan semantics: {plan}", err=True)
                for issue in semantic_issues:
                    typer.echo(
                        f"- [{issue.code}] {issue.path}: {issue.message}",
                        err=True,
                    )
            raise typer.Exit(code=1)

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
    if spec is not None:
        result["spec"] = str(spec)
        result["spec_id"] = openapi_spec.spec_id
        result["semantic"] = True
    if json_output:
        typer.echo(json.dumps(result))
    else:
        typer.echo(
            "Valid TestPlan: "
            f"{result['scenarios']} scenarios, {result['steps']} steps, "
            f"{result['relations']} relations"
            + (f"; OpenAPI semantics match {result['spec_id']}" if spec is not None else "")
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
