# Shadow intelligence and active learning v0.1

The monitor remains safe and useful without local intelligence. Its durable runtime path is:

```text
canonical listing -> deterministic facts and hard blockers -> scoring / alerts / state / delivery
```

Local intelligence is an explicitly opt-in observation path:

```text
canonical listing -> deterministic facts and hard blockers -> semantic routing ->
embedding -> structured local assessment -> agent/RAG -> persisted shadow provenance
```

It never changes a deterministic assessment, blocker, score, recommendation, alert decision,
notification queue, or external delivery. GitHub Actions remains deterministic-only and does not
contact laptop-local Ollama.

## Enabling a bounded local collection

Set both `intelligence.enabled: true` and `intelligence.shadow.enabled: true` in a private local
profile, then explicitly pass `--shadow-intelligence`:

```bash
uv run internship-monitor run \
  --profile config.local/profile.yaml \
  --companies config.local/companies.yaml \
  --shadow-intelligence \
  --rag-index state/rag.sqlite3 \
  --embedding-cache state/embeddings.sqlite3
```

The default cap is 24 observations per run and the default retention is 180 days. The selection
order is deterministic: `review_prior`, internship/student evidence with a semantic negative,
adjacent role, changed semantic content, then any future near-boundary signal. Hard-blocked and
obvious deterministic positives/negatives are skipped. A dry run may exercise the path when
explicitly requested, but it never persists shadow records.

## Provenance and review

Shadow state is append-only within its retention window and deduplicates an unchanged listing plus
unchanged deterministic prior and intelligence contract. The semantic fingerprint covers title,
description, employment type, location, and workplace type; it intentionally excludes discovery
timestamps. The contract fingerprint includes the semantic contract version, model settings, tool
manifest, and a non-reversible RAG-index fingerprint.

Only safe metadata is retained: canonical private review snapshot, provider/model, role proposal,
confidence, grounding status, typed failure/policy outcome, latency, and tool/retrieval document
IDs. Prompts, raw model output, hidden reasoning, corpus excerpts, exception bodies, and secrets
are never stored.

Use the read-only review queue:

```bash
uv run internship-monitor intelligence-review-candidates --state state/jobs.sqlite3
```

It prints summaries only. A private export with descriptions requires both `--export` under
`evaluation.local/` and `--include-descriptions`; every exported row uses
`shadow-review-candidate-v1` and `label_status: not_labeled`.

## Contamination boundary

Shadow predictions are not human truth. They are never written to either frozen human-gold dataset,
never imported into RAG as labeled examples, and never treated as training data automatically. The
intended lifecycle is:

```text
shadow -> explicit human review -> independently labeled corpus -> evaluation ->
optional promotion-only runtime -> fine-tuning only when justified
```

## Operational safety (Session 28.4)

run --shadow-intelligence --shadow-limit N accepts only 1..24 and changes no
profile state. Explicit CLI shadow runs perform a cheap Ollama endpoint/model preflight
before candidate inference; an unavailable provider skips the batch without creating
per-listing fallback observations. Infrastructure-only, no-proposal observations stay in
diagnostics but are excluded from ordinary human-review ranking and do not suppress a later
healthy semantic reassessment. Progress is written only for explicit shadow runs and omits
descriptions, prompts, model output, and retrieval excerpts.
