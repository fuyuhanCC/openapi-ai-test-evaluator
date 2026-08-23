"""OATE command-line application."""

import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from openapi_ai_test_evaluator.domain import OpenAPISpec, TestPlan
from openapi_ai_test_evaluator.domain.execution import ExecutionOutcome
from openapi_ai_test_evaluator.execution import execute_test_plan
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


@app.command("run")
def run_plan(
    spec: Annotated[
        Path,
        typer.Option("--spec", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    plan: Annotated[
        Path,
        typer.Option("--plan", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    base_url: Annotated[
        str,
        typer.Option("--base-url", help="Explicit HTTP(S) base URL of the test target."),
    ],
    out: Annotated[
        Path | None,
        typer.Option("--out", file_okay=True, dir_okay=False),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the complete RunResult JSON."),
    ] = False,
    allow_mutations: Annotated[
        bool,
        typer.Option(
            "--allow-mutations",
            help="Confirm that POST/PUT/PATCH/DELETE target an isolated test environment.",
        ),
    ] = False,
) -> None:
    """Validate and execute one TestPlan against an explicit target URL."""
    test_plan, openapi_spec = _load_validated_run_inputs(plan, spec, json_output)
    try:
        result = execute_test_plan(
            test_plan,
            openapi_spec,
            base_url,
            allow_mutations=allow_mutations,
        )
    except ValueError as error:
        _report_run_input_error("runner", base_url, error, json_output)

    serialized = result.model_dump_json(indent=2)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{serialized}\n", encoding="utf-8")

    if json_output:
        typer.echo(serialized)
    else:
        destination = f"; wrote {out}" if out is not None else ""
        typer.echo(
            f"Run {result.run_id}: {result.outcome.value} "
            f"({len(result.scenarios)} scenario(s), {result.duration_ms} ms){destination}"
        )

    if result.outcome is not ExecutionOutcome.PASSED:
        raise typer.Exit(code=1)


def _load_validated_run_inputs(
    plan_path: Path,
    spec_path: Path,
    json_output: bool,
) -> tuple[TestPlan, OpenAPISpec]:
    try:
        test_plan = load_test_plan(plan_path)
    except PlanLoadError as error:
        _report_run_input_error("plan", str(plan_path), error, json_output)

    try:
        openapi_spec = load_openapi(spec_path)
    except SpecLoadError as error:
        _report_run_input_error("openapi", str(spec_path), error, json_output)

    semantic_issues = validate_plan_semantics(test_plan, openapi_spec)
    if semantic_issues:
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "valid": False,
                        "stage": "semantic",
                        "plan": str(plan_path),
                        "spec": str(spec_path),
                        "issues": [issue.model_dump() for issue in semantic_issues],
                    }
                )
            )
        else:
            typer.echo(f"Invalid TestPlan semantics: {plan_path}", err=True)
            for issue in semantic_issues:
                typer.echo(
                    f"- [{issue.code}] {issue.path}: {issue.message}",
                    err=True,
                )
        raise typer.Exit(code=1)
    return test_plan, openapi_spec


def _report_run_input_error(
    stage: str,
    path: str,
    error: Exception,
    json_output: bool,
) -> NoReturn:
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "valid": False,
                    "stage": stage,
                    "path": path,
                    "error": str(error),
                }
            )
        )
    else:
        typer.echo(f"Cannot run TestPlan ({stage}): {path}\n{error}", err=True)
    raise typer.Exit(code=1)


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
            help="Also validate the plan against a supported OpenAPI 3.0/3.1 document.",
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
