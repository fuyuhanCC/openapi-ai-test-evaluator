# OpenAPI AI Test Generation and Fault Evaluation Framework

An experimental framework for generating declarative API tests from OpenAPI
documents and evaluating their fault-detection capability with deterministic
oracles.

> **Current status:** provider-independent TestCaseBatch generation and execution
> are implemented for the supported OpenAPI 3.0/3.1 scope. The generation path
> includes normalized OpenAPI context, a versioned prompt, a DeepSeek HTTP
> adapter, structural and semantic output validation, and separate validated,
> metadata, and raw-output artifacts. The deterministic HTTP runner supports assertions,
> extraction, setup/main/cleanup sequencing, and metamorphic/lifecycle
> relations. Schemathesis integration, benchmark services, fault injection, and
> aggregate experiment reports remain to be implemented.

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
- Deterministic TestCaseResult/RunResult 2.0 aggregation with explicit skipped-step
  artifacts, best-effort cleanup handling, timestamps, and fault metadata.
- `oate cases validate` and `oate cases run` commands with structural and
  pre-execution semantic validation, explicit target
  URL validation, mutation opt-in, JSON output, and CI-friendly exit codes.
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

## Generate Test Cases with DeepSeek

Set the API key in the process environment. It is never included in a
ProviderRequest or generation artifact:

```bash
export DEEPSEEK_API_KEY="your-key"
```

Generate a validated TestCaseBatch, a GenerationRecord, and the unvalidated
provider output used to produce them:

```bash
uv run oate cases generate \
  --spec examples/demo-items/openapi.yaml \
  --provider deepseek \
  --model deepseek-v4-flash \
  --generation-id deepseek-demo-001 \
  --cases-output artifacts/cases/deepseek-demo-001.json \
  --record-output artifacts/generations/deepseek-demo-001.json \
  --raw-output artifacts/raw/deepseek-demo-001.txt
```

The cases artifact is written only when the model output passes structural,
generation-limit, and OpenAPI semantic validation. The GenerationRecord is
written for both successful and failed provider attempts. Whenever the provider
returns content, that unvalidated text is also preserved—even if it fails later
validation—so failed generations can be inspected and reproduced. The default
`api-cases-v3` prompt explicitly counts setup, main, and cleanup requests toward
the same per-case step limit and demonstrates the object syntax required for
runtime variable references. Existing artifact files are not replaced unless
`--overwrite` is explicitly provided.

Validate and run a generated batch with the same runner used by every adapted
generator:

```bash
uv run oate cases validate \
  --spec examples/demo-items/openapi.yaml \
  --cases artifacts/cases/deepseek-demo-001.json

uv run oate cases run \
  --spec examples/demo-items/openapi.yaml \
  --cases artifacts/cases/deepseek-demo-001.json \
  --base-url http://127.0.0.1:8000
```

For a deterministic local target, start the FastAPI fixture in a separate
terminal before running the commands above:

```bash
uv run uvicorn services.demo_items.app:app --host 127.0.0.1 --port 8000
```

The fixture implements all six operations in the demo OpenAPI document with an
in-memory store. `POST /__test__/reset` clears its state and restarts IDs for a
repeatable run. Generated batches containing mutations still require
`--allow-mutations` when passed to `oate cases run`.

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

## Run a TestPlan

Execute a validated read-only plan and print a concise summary:

```bash
uv run oate run \
  --spec examples/demo-items/openapi.yaml \
  --plan examples/plans/minimal-get.yaml \
  --base-url http://127.0.0.1:8000
```

Write the canonical RunResult JSON and also emit it to stdout:

```bash
uv run oate run \
  --spec examples/demo-items/openapi.yaml \
  --plan examples/plans/minimal-get.yaml \
  --base-url http://127.0.0.1:8000 \
  --out artifacts/manual-run.json \
  --json
```

Plans containing `POST`, `PUT`, `PATCH`, or `DELETE` are rejected unless the
caller adds `--allow-mutations` to confirm that the target is an isolated test
environment. A failed or errored run still writes its result and exits nonzero.

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
