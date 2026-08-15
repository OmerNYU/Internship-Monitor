# Evaluation ground truth and provider trace v0.1

## Two intentionally separate datasets

`evaluation/gold.example.v1.jsonl` remains the tracked synthetic **contract/regression** fixture. It verifies deterministic behaviour and hard-block invariants; because its labels match the deterministic baseline, it is not evidence that intelligence improves quality.

Human quality measurements use the separate `human_gold_v1` JSONL contract. Private real cases belong in ignored `evaluation.local/human_gold.jsonl`; the tracked `evaluation/human_gold.example.v1.jsonl` is sanitized and only demonstrates format. Only `human` or `human_reviewed` provenance is accepted. Tooling never calls monitor predictions human labels.

## Human-gold fields

A case contains a stable ID, sanitized source identity, canonical listing evidence, human rationale, provenance, and `expected`. `expected` supports relevance (`relevant`, `maybe`, `irrelevant`), hard-block boolean and reason, role family, geographic bucket, strength, authorization, language, season, and graduation. Each uncertain/unjudgable field explicitly uses `unknown` or `not_labeled`; only labeled fields are compared.

## Current internship season scope

Internship Monitor is internship-only. Its current search window is Winter 2026/27 where an
employer offers that term, Spring 2027, and Summer 2027, with Summer 2027 primary. Season
compatibility is assessed separately from internship relevance: a compatible date must not make a
new-grad, graduate-program, full-time, or other non-internship listing relevant.

Bare `Winter 2026` wording remains ambiguous because employers name winter terms differently. It
requires a cross-year label or explicit late-2026/early-2027 dates before it is treated as the
Winter 2026/27 term. Listings with no explicit term or bounded internship date range remain
`unknown`, not compatible.

## Local workflow

1. Export a private canonical snapshot from a dry monitor run, or supply an already available local JSONL. For example: `internship-monitor run --dry-run --profile config.local/profile.yaml --companies config.local/companies.yaml --export-listings evaluation.local/dogfood_listings.jsonl`. The export includes every successfully normalized listing, not only alert candidates, and must remain local/private.
2. For a fresh generic sample, run `internship-monitor evaluate-curate --input INPUT --output evaluation.local/human_gold.jsonl --seed 26 --limit 40`. For a Session 27 pilot replacement, preserve reviewed records and balance the remaining templates: `internship-monitor evaluate-curate --input evaluation.local/dogfood_listings.jsonl --output evaluation.local/human_gold_balanced.jsonl --preserve evaluation.local/human_gold.jsonl --limit 40 --seed 26 --balanced`. Balanced mode uses deterministic evidence only to stratify high-recall student, borderline, and geography/authorization/language cases; it never fills expected labels or reuses preserved source identities.
3. Independently edit the blank templates: replace `not_labeled` only where evidence supports a judgment, write a rationale, and change provenance from `template` to `human` or `human_reviewed`. Curation emits no monitor decision or expected result.
4. Run `internship-monitor validate-human-gold --dataset evaluation.local/human_gold.jsonl`.
5. Compare a provider with `internship-monitor evaluate --human-gold --dataset evaluation.local/human_gold.jsonl --provider deterministic|embedding|llm|agent`.

## Provider trace and privacy

Every assessment carries an additive ordered trace: deterministic, embedding, structured LLM, RAG, and agent stages that ran or were considered. A stage records its safe status, model identity, role transition, promotion, fallback/error category, tool names/count, retrieval count, and document IDs only. It never includes private corpus contents, listing text, secrets, or exception dumps. Later fallback stages append rather than overwrite earlier success.

Session 27 may aggregate the independent human-gold comparisons and safe stage statuses, but must not use regression-fixture agreement as a quality result.


## Session 27 offline ablation

Run the independent private pilot through the same immutable human-gold cases for each available provider chain:

```text
uv run internship-monitor evaluate --human-gold --dataset evaluation.local/human_gold.jsonl --profile config.local/profile.yaml --ablation --output evaluation.local/session27_ablation.json --report evaluation.local/session27_report.md
```

The JSON and Markdown artifacts stay under ignored `evaluation.local/`. They contain aggregate and case-level safety metrics, safe provider-trace status summaries, and titles/company identities needed for review; they deliberately omit listing descriptions, private RAG excerpts, corpus text, credentials, and exception dumps. No dataset records are written or relabeled.

The comparisons are truthful to the current chain: deterministic; embedding; structured LLM (which wraps embedding); and the bounded agent with RAG. There is no standalone LLM-plus-RAG provider, so the agent result is the only RAG-consuming ablation. Provider unavailability, malformed output, and fallback are recorded from the additive trace rather than simulated.

The report separates two questions. **Semantic role relevance** compares the existing `RoleMatchLevel` with a human semantic label where that label is safely inferable: an unblocked human label is usable directly; a hard-blocked final-negative is semantic-positive only when its title explicitly identifies an internship and its human role family is an approved target/adjacent family; a hard-blocked non-internship is semantic-negative; other blocked cases are explicitly `indeterminate` and excluded from semantic denominators. Strict semantic positives are `strong_match` and `relevant`; broad positives additionally include `review`.

**Final opportunity relevance** compares the human `expected.relevance` field with the same role result after deterministic hard blockers. A blocked listing is always final `irrelevant`, even when it is a semantic target-role match. This prevents a Fall-2026 SWE internship from appearing as a final false positive merely because its role match is correct. Incorrect hard blocks and missed human blockers remain independent safety metrics and are never hidden by final relevance agreement.

For provider comparisons, artifacts separately report beneficial/harmful semantic promotions, blocked semantic promotions, and beneficial/harmful final changes. AI promotions do not override blockers. The legacy top-level `relevant_recall`, `relevant_or_maybe_recall`, `false_negative_ids`, `relevance_confusion`, and `promotion_summary` fields remain for compatibility; consumers should use the additive `semantic_role`, `final_opportunity`, `safety`, and `provider_effects` JSON objects for unambiguous semantics. Unknown and not-labeled human dimensions are excluded from agreement denominators. The pilot recommendation uses final strict recall among zero-incorrect-block chains only as directional evidence, not a production calibration or a change to monitor scoring, routing, notification, or alert behavior.
