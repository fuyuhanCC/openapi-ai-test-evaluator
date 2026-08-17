# OpenAPI AI Test Generation and Fault Evaluation Framework

An experimental framework for generating declarative API tests from OpenAPI
documents and evaluating their fault-detection capability with deterministic
oracles.

> **Current status:** the V1 design, TestPlan contract, OpenAPI 3.0 loader, and
> plan-to-spec semantic validation are implemented. HTTP execution, DeepSeek
> integration, PetClinic, and fault injection are planned next.

## What works today

- Strict Pydantic models for the declarative TestPlan format.
- OpenAPI 3.0 validation, local-reference resolution, and operation normalization.
- Structural TestPlan validation plus plan-to-OpenAPI semantic validation.
- Positive, negative, stateful, and metamorphic TestPlan examples.
- Generated JSON Schema for external tools and structured model output.
- A CLI for validating plans independently or against an OpenAPI document.
- Unit tests for valid and invalid contract behavior.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python 3.12, installed and managed automatically by uv

## Setup

```bash
uv sync
```

## Validate a TestPlan

Check only the generator-independent TestPlan structure:

```bash
uv run oate plan validate --plan examples/plans/metamorphic.yaml
```

Also check operation IDs, parameters, request schemas, response contracts, and
metamorphic references against an OpenAPI document:

```bash
uv run oate plan validate \
  --spec examples/demo-items/openapi.yaml \
  --plan examples/plans/all-methods.yaml
```

Export the generated JSON Schema:

```bash
uv run oate plan schema --out schemas/test-plan.schema.json
```

## Development checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
```

## Documentation

See [docs/design.md](docs/design.md) for the V1 architecture, experiment design,
supported OpenAPI scope, and acceptance criteria.
