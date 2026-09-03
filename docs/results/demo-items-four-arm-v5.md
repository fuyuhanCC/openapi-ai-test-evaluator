# Demo Items Four-Arm Experiment

This page records the first frozen experiment result produced by the framework.
It is a concise, reviewable snapshot of the generated report, not a claim that
one generator is universally better than another.

## Question

The experiment asks two separate questions:

1. How do DeepSeek-generated API cases and Schemathesis-generated API cases
   behave when they pass through the same validation, execution, and evaluation
   pipeline?
2. Does adding the same seven lifecycle/metamorphic cases improve either native
   suite's ability to detect faults that isolated requests miss?

## Method

- Target: the in-repository Demo Items FastAPI service with six OpenAPI
  operations.
- Generators: `deepseek-v4-flash` and Schemathesis 4.25.2.
- Repetitions: three independent DeepSeek generations and three Schemathesis
  seeds (7, 8, and 9).
- Arms: each generator's native cases, plus the same seven shared relation
  cases.
- Environments: one clean service and four deterministic response faults.
- Control: every adapted case uses the same `TestCaseBatch` contract, runner,
  assertions, reset behavior, fault proxy, and evaluator.
- Budget: suite sizes are deliberately not equalized; raw and normalized
  request metrics are both reported.

The checked-in benchmark definition is
[`benchmarks/demo_items/frozen/v5/benchmark.yaml`](../../benchmarks/demo_items/frozen/v5/benchmark.yaml).
It references the sanitized, checked-in inputs under
[`benchmarks/demo_items/frozen/v5/`](../../benchmarks/demo_items/frozen/v5/),
so the exact case execution and evaluation can be replayed without calling
DeepSeek. A new provider generation is intentionally treated as a new
repetition rather than as a bit-for-bit reproduction.

## Results

Values below are mean +/- population standard deviation across three
repetitions.

| Suite | Received / native admitted | Executed / shared | Admission | Operation coverage | Clean false positives | Fault detection | Detected / 100 fault requests | Total requests | Generation time | Estimated API cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DeepSeek + shared relations | 21.0 +/- 0.0 / 16.7 +/- 1.2 | 23.7 +/- 1.2 / 7.0 +/- 0.0 | 79.4% +/- 5.9% | 100.0% +/- 0.0% | 0.0% +/- 0.0% | 100.0% +/- 0.0% | 1.96 +/- 0.17 | 258.3 +/- 20.9 | 24.00 s +/- 3.49 s | $0.006783 +/- $0.003129 |
| DeepSeek native | 21.0 +/- 0.0 / 16.7 +/- 1.2 | 16.7 +/- 1.2 / 0.0 +/- 0.0 | 79.4% +/- 5.9% | 94.4% +/- 7.9% | 0.0% +/- 0.0% | 75.0% +/- 0.0% | 3.28 +/- 0.64 | 118.3 +/- 20.9 | 24.00 s +/- 3.49 s | $0.006783 +/- $0.003129 |
| Schemathesis + shared relations | 296.0 +/- 0.0 / 223.3 +/- 2.5 | 230.3 +/- 2.5 / 7.0 +/- 0.0 | 75.5% +/- 0.8% | 100.0% +/- 0.0% | 0.0% +/- 0.0% | 100.0% +/- 0.0% | 0.40 +/- 0.00 | 1256.7 +/- 12.5 | 0.306 s +/- 0.006 s | $0.000000 |
| Schemathesis native | 296.0 +/- 0.0 / 223.3 +/- 2.5 | 223.3 +/- 2.5 / 0.0 +/- 0.0 | 75.5% +/- 0.8% | 100.0% +/- 0.0% | 0.0% +/- 0.0% | 75.0% +/- 0.0% | 0.34 +/- 0.00 | 1116.7 +/- 12.5 | 0.306 s +/- 0.006 s | $0.000000 |

Schemathesis has zero paid model-API cost in this report; local CPU time is
represented by generation and execution durations rather than a dollar price.
DeepSeek cost is estimated from frozen token usage and the versioned pricing
snapshots in the benchmark configuration.

## Per-fault stability

| Fault | DeepSeek native | DeepSeek enhanced | Schemathesis native | Schemathesis enhanced |
| --- | ---: | ---: | ---: | ---: |
| `get-id-as-string` | 3/3 | 3/3 | 3/3 | 3/3 |
| `get-missing-name` | 3/3 | 3/3 | 3/3 | 3/3 |
| `get-status-error` | 3/3 | 3/3 | 3/3 | 3/3 |
| `list-duplicate-first-item` | 0/3 | 3/3 | 0/3 | 3/3 |

## Interpretation

- The native suites tie on fault detection: both detect three of four faults in
  every repetition. This experiment does not show that DeepSeek detects more
  faults than Schemathesis.
- DeepSeek achieves the native result with substantially fewer requests and a
  higher detected-faults-per-request value. Because the suites were not run
  under an equal request budget, this is an efficiency signal rather than a
  controlled causal comparison.
- Both native suites miss the duplicated-list-item fault. Adding the identical
  relation pack makes both detect it in all repetitions, showing the value of
  the shared lifecycle/metamorphic oracle rather than an advantage belonging to
  either generator.
- No suite reports a failure against the clean service in this run.
- DeepSeek admits 15 to 18 of 21 generated cases per repetition. Rejected cases
  remain part of generator-quality reporting instead of being silently dropped.

## Limitations

- Demo Items is a small controlled service, not an external production-style
  application.
- The fault catalog contains four response mutations; it does not represent all
  API failure modes.
- Three repetitions are enough to exercise the reporting pipeline but not to
  support broad statistical claims.
- Only one LLM provider/model and one conventional schema-based tool are
  included.
- Unequal native suite sizes make this an ecological comparison of default
  generator behavior, not an equal-budget comparison.
- The estimated LLM cost follows the captured pricing snapshot and may differ
  from the provider's final bill.

The next evidence step is to repeat the experiment on an external benchmark
such as PetClinic and, if generator efficiency becomes the research question,
add a separately labelled equal-request-budget experiment.
