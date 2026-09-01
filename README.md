# OpenAPI AI Test Generation and Fault Evaluation Framework

An experimental framework for generating declarative API tests from OpenAPI
documents and evaluating their fault-detection capability with deterministic
oracles.

> **Current status:** provider-independent TestCaseBatch generation and execution
> are implemented for the supported OpenAPI 3.0/3.1 scope. DeepSeek and
> Schemathesis both produce the same runner-ready contract with separate raw,
> generation, and adaptation artifacts. The deterministic HTTP runner supports
> assertions, extraction, setup/main/cleanup sequencing, and
> metamorphic/lifecycle relations. Response fault injection, clean-versus-fault
> orchestration, single-suite evaluation, and multi-repetition comparison
> reports are implemented and exercised in a three-repetition, four-arm Demo
> Items experiment. Docker Compose/CI packaging and an external PetClinic
> benchmark remain to be implemented.

## Experiment snapshot

The completed Demo Items experiment compares native DeepSeek and Schemathesis
cases, then adds the same seven lifecycle/metamorphic cases to each generator.
Every suite is executed by the same runner against a clean service and four
deterministic response faults.

| Suite | Native admitted / executed | Operation coverage | Clean false positives | Fault detection | Mean requests | Mean API cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DeepSeek native | 16.7 / 16.7 | 94.4% | 0.0% | 75.0% | 118.3 | $0.006783 |
| DeepSeek + shared relations | 16.7 / 23.7 | 100.0% | 0.0% | 100.0% | 258.3 | $0.006783 |
| Schemathesis native | 223.3 / 223.3 | 100.0% | 0.0% | 75.0% | 1116.7 | $0.000000 |
| Schemathesis + shared relations | 223.3 / 230.3 | 100.0% | 0.0% | 100.0% | 1256.7 | $0.000000 |

Values are means across three independently generated/adapted repetitions.
Suite sizes were not equalized, so request counts remain part of the result
rather than being hidden behind a common budget. See the
[experiment result and limitations](docs/results/demo-items-four-arm-v5.md) for
the full interpretation.

## What works today

- Strict Pydantic models for the declarative TestPlan format.
- OpenAPI 3.0.x/3.1.x common-subset validation, local-reference resolution, and
  operation normalization.
- Mature OpenAPI validators for document conformance and static Schema value
  checking, with project-specific error mapping for TestPlan semantics.
- Structural TestPlan validation plus plan-to-OpenAPI semantic validation.
- DeepSeek generation and Schemathesis examples/coverage/fuzzing adaptation into
  the common `TestCaseBatch` contract.
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
- A deterministic FastAPI response-fault proxy with pass-through mode,
  single-fault activation, bounded upstream responses, trigger counts, and
  status/JSON mutation operators.
- A strict four-fault Demo Items catalog with reference tests proving that each
  fault is triggerable and produces an observable response difference.
- Clean-versus-fault suite orchestration that resets the SUT, executes the same
  frozen batch in every state, records proxy trigger evidence, and restores
  pass-through mode after the run.
- Strict per-suite `EvaluationResult` metrics that keep native generator
  admission separate from shared enhancement and total executed case counts,
  plus clean false positives, operation coverage, request counts, and fault
  detection with same-case proxy evidence.
- A single-suite pipeline that connects one frozen batch to clean/fault
  execution and evaluation, then preserves the clean run, every fault run, and
  `EvaluationResult` as separate JSON artifacts without overwriting by default.
- Strict multi-suite `ComparisonResult` aggregation across paired repetitions,
  including raw suite size, normalized efficiency, mean/standard deviation,
  missing-value accounting, and per-fault outcome stability.
- A strict `BenchmarkConfig` YAML and `oate benchmark run --config` command that
  preflights every paired input before HTTP traffic, runs suites sequentially,
  preserves per-run artifacts, and writes the final comparison automatically.
- `oate report compare` output in machine-readable JSON and human-readable
  Markdown, preserving every source evaluation ID for traceability.
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

The cases artifact is written when the provider returns a valid batch envelope
with at least one independently admitted case. Each case passes structural,
generation-limit, and OpenAPI semantic validation; rejected cases remain in the
raw output and are counted with stable reasons in `GenerationRecord.case_admission`.
The GenerationRecord is written for both successful and failed provider attempts.
Whenever the provider returns content, that unvalidated text is also preserved,
so failed and partially admitted generations can be inspected. DeepSeek uses
the Responses API with `json_schema` output for the complete `TestCaseBatch`
contract. The default `api-cases-v5` prompt explicitly counts setup, main, and
cleanup requests toward the same per-case step limit, demonstrates the object
syntax required for runtime variable references, prohibits programming
expressions in place of literal JSON values, and tells the model to omit an
impractical long-string boundary rather than abbreviating it as executable code.
The default output budget is 8192 tokens. Existing artifact files are not
replaced unless `--overwrite` is explicitly provided.

Generate the conventional Schemathesis baseline without sending requests to the
target service:

```bash
uv run oate cases generate-baseline \
  --spec examples/demo-items/openapi.yaml \
  --tool schemathesis \
  --cases-output artifacts/cases/schemathesis-demo-001.json \
  --record-output artifacts/adaptations/schemathesis-demo-001.json \
  --seed 7
```

This command draws all finite examples and coverage cases plus a bounded number
of positive/negative fuzzing cases for every OpenAPI operation. It adapts
eligible requests into the common `TestCaseBatch` and records every rejected
case category in an `AdaptationRecord`. HTTP requests are sent only later
through `oate cases run`.

Compose either native generator batch with the same shared enhancement pack:

```bash
uv run oate cases compose \
  --base-cases artifacts/cases/deepseek-demo-001.json \
  --enhancement-cases benchmarks/demo_items/enhancements/shared-relations.yaml \
  --pack-id shared-relations \
  --composition-id deepseek-demo-001-enhanced \
  --cases-output artifacts/cases/deepseek-demo-001-enhanced.json \
  --record-output artifacts/compositions/deepseek-demo-001-enhanced.json
```

The command performs no model or HTTP calls. It prefixes every enhancement case
ID with its pack ID, preventing ordinary collisions with generator-produced
case IDs, and rejects any collision that remains after namespacing. It writes a
`SuiteCompositionRecord` containing native, enhancement, and composed case
counts plus canonical SHA-256 values. Run the same command with the Schemathesis
batch to build the paired enhanced arm from identical shared cases.

Start the Demo Items SUT and fault proxy in two separate terminals:

```bash
uv run uvicorn services.demo_items.app:app --host 127.0.0.1 --port 8000
```

```bash
OATE_FAULT_PROXY_UPSTREAM=http://127.0.0.1:8000 \
OATE_FAULT_PROXY_FAULTS=benchmarks/demo_items/faults \
uv run uvicorn services.fault_proxy.app:app --host 127.0.0.1 --port 8001
```

Then run and evaluate one already-generated Schemathesis suite. Repeat `--fault`
for every fault in the frozen catalog:

```bash
uv run oate benchmark run-suite \
  --spec examples/demo-items/openapi.yaml \
  --cases artifacts/cases/schemathesis-demo-001.json \
  --source-record artifacts/adaptations/schemathesis-demo-001.json \
  --suite-id schemathesis \
  --repetition 1 \
  --runner-base-url http://127.0.0.1:8001 \
  --proxy-control-url http://127.0.0.1:8001 \
  --sut-reset-url http://127.0.0.1:8000/__test__/reset \
  --fault get-id-as-string \
  --fault get-missing-name \
  --fault get-status-error \
  --fault list-duplicate-first-item \
  --output-directory artifacts/runs/schemathesis-r1 \
  --allow-mutations
```

Once all native and enhanced input artifacts exist, the complete four-arm
matrix can instead be run with one command while the same two services are up:

```bash
uv run oate benchmark run \
  --config benchmarks/demo_items/final-four-arm-v5.yaml
```

Paths inside the config are resolved relative to the config file. Every suite
lists a separate frozen input for each repetition, so repeated LLM generations
can be compared without pretending that rerunning one fixed batch measures
generation variability. The checked-in final configuration runs the four
native/enhanced arms across three paired repetitions. LLM inputs reference a
versioned `pricing_id`; the corresponding top-level snapshot records the
provider, model, peak/off-peak rate class, cached and uncached input rates,
output rate, capture time, and source URL. Cost is derived from frozen token
usage without modifying the original generation record.

The command writes the clean `RunResult`, one `RunResult` per fault, and the
derived `EvaluationResult` to separate files. Existing output directories are
rejected unless `--overwrite` is explicit. Run the same command with the
DeepSeek cases and `GenerationRecord`. For an enhanced arm, point `--cases` to
the composed batch and also pass its receipt, for example
`--composition-record artifacts/compositions/deepseek-demo-001-enhanced.json`.
The evaluator checks native counts, composed counts, and the composed batch
SHA-256 before sending requests. Then compare the paired evaluations:

```bash
uv run oate report compare \
  --evaluation artifacts/runs/deepseek-r1/evaluation/evaluation.json \
  --evaluation artifacts/runs/schemathesis-r1/evaluation/evaluation.json \
  --comparison-id deepseek-vs-schemathesis \
  --json-output artifacts/reports/comparison.json \
  --markdown-output artifacts/reports/comparison.md
```

Repeat `--evaluation` for every suite and repetition. Each suite must contain
the same repetition numbers and fault IDs. The report keeps unequal native
suite sizes visible and presents both raw request counts and normalized metrics.

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
supported OpenAPI scope, and acceptance criteria. See
[docs/results/demo-items-four-arm-v5.md](docs/results/demo-items-four-arm-v5.md)
for the frozen three-repetition Demo Items result.
