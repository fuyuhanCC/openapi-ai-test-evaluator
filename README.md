# OpenAPI AI Test Generation and Fault Evaluation Framework

An experimental framework for generating declarative API tests from OpenAPI
documents and evaluating their fault-detection capability with deterministic
oracles.

> **Current status:** the V1 contracts, OpenAPI 3.0/3.1 common-subset loader,
> plan-to-spec semantic validation, and core HTTP execution pipeline are
> implemented. Deterministic declarative assertions and response extraction are
> also implemented and coordinated into single-step execution. Scenario-local
> setup/main sequencing, variable propagation, and conditional cleanup are
> implemented. All three V1 metamorphic relations and all three lifecycle
> consistency checks are evaluated over real step executions; RunResult
> aggregation, DeepSeek integration, PetClinic, and fault injection are planned
> next. There is not yet an `oate run` command.

## What works today

- Strict Pydantic models for the declarative TestPlan format.
- OpenAPI 3.0.x/3.1.x common-subset validation, local-reference resolution, and
  operation normalization.
- Mature OpenAPI validators for document conformance and static Schema value
  checking, with project-specific error mapping for TestPlan semantics.
- Structural TestPlan validation plus plan-to-OpenAPI semantic validation.
- Positive, negative, stateful, and metamorphic TestPlan examples.
- Strict RunResult contracts for requests, responses, assertions, extractions,
  relations, faults, and structured errors.
- Deterministic request building and bounded HTTPX transport without retries or
  redirect following.
- Runtime request and response contract validation delegated to `openapi-core`,
  including OpenAPI 3.0 and 3.1 fixtures.
- Response parsing, partial-evidence preservation, and sanitized request and
  response snapshots.
- Deterministic allowlisted assertions over response status, headers, and JSON
  values, with runtime variable resolution and sanitized result evidence.
- Deterministic response extraction into runtime values, with required/optional
  missing-value handling and separately sanitized RunResult evidence.
- Single-step execution coordination with stable outcomes and structured errors
  for request, transport, assertion, and extraction failures.
- Isolated scenario variable scopes with ordered setup/main execution, extracted
  value propagation, and deterministic stop-on-failure behavior.
- Conditional `always`/`on_success`/`on_failure` cleanup execution with explicit
  skipped results and required versus best-effort outcome policies.
- Relation value selection from executed request bodies, response bodies, and
  response statuses, with raw in-memory values and sanitized result snapshots.
- Runtime evaluation of repeated-read consistency, query-parameter order
  invariance, and pagination monotonicity, including explicit `not_applicable`,
  `failed`, and `error` outcomes.
- Runtime evaluation of create-read, update-read, and delete-read lifecycle
  consistency, including stable-field checks against a pre-update baseline.
- Unified scenario-relation dispatch in TestPlan declaration order before
  cleanup mutates or removes observed resources.
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
scenario relations against an OpenAPI document:

```bash
uv run oate plan validate \
  --spec examples/demo-items/openapi.yaml \
  --plan examples/plans/all-methods.yaml
```

The equivalent OpenAPI 3.1 fixture uses the same TestPlan contract and operation
IDs:

```bash
uv run oate plan validate \
  --spec examples/demo-items/openapi-3.1.yaml \
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
