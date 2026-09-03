# Frozen Demo Items V5 Inputs

This directory contains the configuration and sanitized inputs used by the
published Demo Items four-arm result. Its entry point is `benchmark.yaml`.

## Contents

- `benchmark.yaml`: complete suite matrix, endpoints, faults, pricing snapshots,
  frozen input references, and output locations.
- `cases/`: native and shared-relation-enhanced `TestCaseBatch` files for three
  DeepSeek generations and three Schemathesis seeds.
- `generations/`: DeepSeek generation metadata, token usage, timing, and case
  admission records.
- `adaptations/`: Schemathesis version, seed, timing, and adaptation records.
- `compositions/`: hashes and counts proving how each enhanced suite was built.
- `raw/`: the three structured DeepSeek responses retained for admission
  auditing; the benchmark runner does not read these files.
- `SHA256SUMS`: integrity hashes for every frozen data file.

The bundle contains no provider API key, authorization header, password, or
`.env` value. Provider request IDs are retained as provenance identifiers.

Replaying the frozen benchmark measures the current runner and evaluator using
the original cases. It does not reproduce the probabilistic provider call.
Generating new cases creates a new experimental repetition and may produce a
different case batch even with the same prompt and model name.

Verify that the frozen files are unchanged with:

```bash
cd benchmarks/demo_items/frozen/v5
shasum -a 256 -c SHA256SUMS
```

From the repository root, replay the experiment with:

```bash
uv run oate benchmark run \
  --config benchmarks/demo_items/frozen/v5/benchmark.yaml
```
