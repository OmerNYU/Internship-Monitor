# Semantic Evaluation Providers v0.1

Sessions 23 and 24 add optional local intelligence only to the explicit
internship-monitor evaluate command. They do not participate in discovery,
scheduled monitoring, persistent state, notification decisions, or delivery.

## Invariants

- Deterministic hard blockers are evaluated first and skip all model calls.
- Geography, authorization, language, graduation, and season assessments stay deterministic.
- Semantic providers can only promote an unblocked ambiguous student role; they cannot demote an
  existing deterministic/embedding role decision or create/remove hard blockers.
- Every promotion is passed through the existing scoring engine. Scores and strength remain
  explainable consequences of typed assessments.
- Invalid, unavailable, malformed, or timed-out local responses retain the prior provider output
  and record a typed fallback reason.

## Embeddings

The embedding provider derives its archetypes only from the configured role preferences and skill
signals. It sends job/archetype text to local Ollama's documented /api/embed endpoint after
confirming the configured model is present. It validates cardinality, finite numeric values, and
matching vector dimensions, then calculates cosine similarity with the Python standard library.

evaluate --provider embedding uses an ignored SQLite cache at state/embeddings.sqlite3 by default.
Entries are keyed by embedding model plus a normalized-text SHA-256 hash, so models never share
vectors. --embedding-cache PATH changes the local cache location; cache use has no effect on
monitoring state.

qwen3-embedding:0.6b is the safe configurable default. The command never pulls a model.

## Structured LLM follow-on

The Session 24 provider uses local /api/chat with a Pydantic JSON Schema, stream false, think
false, and temperature zero. It accepts only role-level, bounded-confidence, source-grounded
evidence. Unknown schema fields, invalid values, missing models, malformed responses, low
confidence, unsupported citations, and timeouts retain the embedding assessment.

The LLM can propose only review or relevant, only when that is a strict promotion over the prior
role level. It cannot modify deterministic geography or eligibility evidence, and it cannot create
or remove a hard blocker. Its default model is configurable as qwen3:4b; the command never
downloads it.

The JSON report includes per-case semantic provider status and fallback reason without emitting
the listing description. Benchmark output is diagnostic: Session 27 will calibrate comparative
quality thresholds. Before proceeding between sessions, require zero expected-retained cases
incorrectly blocked.
