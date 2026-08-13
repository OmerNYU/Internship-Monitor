# Evaluation harness v0.1

Session 21 provides an offline benchmark for deterministic assessment and future intelligence providers. It evaluates typed provider output against independent human labels. It does not discover jobs, persist state, call providers, or send notifications.

## Dataset format

A version-1 dataset is UTF-8 JSONL: one complete `GoldCase` per line. Every record contains:

- `schema_version: 1` and a stable unique `case_id`;
- a canonical `listing` matching the `JobListing` contract;
- an `expected` decision vector: actionability, hard-blocker kinds, role level, geographic bucket, graduation, authorization, language, season, and strength;
- optional `uncertainty_notes` for a retained manual-review case and optional general `notes`.

The loader rejects empty datasets, malformed JSON, duplicate IDs, unknown fields, invalid enum values, and a blocked/retained label whose blocker evidence is inconsistent.

`actionability` is `blocked`, `actionable`, or `manual_review`. A human must label ambiguity as retained `manual_review`, never fabricate certainty or remove the case. Gold labels represent the reviewer decision, not a snapshot of the current deterministic scorer.

The tracked synthetic example is [gold.example.v1.jsonl](../evaluation/gold.example.v1.jsonl). Keep real labeled listings and personal judgments in ignored `config.local/evaluation/`, for example `config.local/evaluation/gold.v1.jsonl`.

## Running the benchmark

```bash
uv run internship-monitor evaluate \
  --dataset evaluation/gold.example.v1.jsonl \
  --profile config/profile.example.yaml

uv run internship-monitor evaluate \
  --dataset config.local/evaluation/gold.v1.jsonl \
  --profile config.local/profile.yaml \
  --json
```

The readable report includes per-field exact-match accuracy, hard-blocker kind precision/recall/F1, mismatch case IDs, and the safety-critical count of retained cases incorrectly blocked. `--json` emits the same typed report as JSON for later experiment tooling.

## Provider boundary and invariants

`AssessmentProvider` synchronously maps a canonical listing to `JobAssessment`. `DeterministicAssessor` is the current baseline and is shared with monitoring orchestration; future embedding and LLM providers must implement the same interface.

Benchmark output is diagnostic only in v0.1. It does not create a CI quality gate. Future providers must preserve explicit deterministic hard blockers, distinguish unknown evidence from incompatible evidence, and never use a low score as deletion.
