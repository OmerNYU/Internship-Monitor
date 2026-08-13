# Local RAG and restricted adjudication v0.1

## Scope and privacy

RAG and adjudication are explicit offline evaluation features. They are not imported by
monitoring, notification, persistence, or delivery workflows. `rag-index` and
`rag-search` only contact the configured local Ollama endpoint and never download a model.
Private Markdown belongs in ignored `config.local/rag/`; the tracked
`evaluation/rag.example/` directory is synthetic documentation only.

## Corpus and index lifecycle

Every private document uses strict YAML front matter with schema version 1, a unique stable
ID, `profile` or `project` kind, title, optional tags, and a non-empty body. Invalid fields,
duplicate IDs, invalid kinds, malformed front matter, and paths escaping the corpus root are
rejected. Validated configuration generates read-only role-archetype and policy documents.
A private gold JSONL file may be supplied explicitly to add `labeled_example` documents;
the tracked synthetic gold fixture is never selected implicitly.

`rag-index --profile ... --corpus-dir ... --index ... --embedding-cache ...` rebuilds the
ignored SQLite metadata index. Text hashes and model-qualified vectors remain in the existing
ignored embedding cache, so unchanged chunks do not need embedding again. `rag-search` returns
bounded excerpts in descending cosine similarity; ties use document ID then chunk index.

## Agent authority and fallbacks

`evaluate --provider agent --rag-index ...` is the sole adjudication entry point. The agent is
disabled by default and is considered only for an unblocked manual-review role at score 40 or
more in a priority market, preferred region, or international remote bucket. It has at most four
local `/api/chat` tool rounds and only five read-only, argument-free tools: job details,
deterministic assessment, profile/project/policy retrieval, role-archetype retrieval, and
optional labeled-example retrieval.

The final strict response must propose `review` or `relevant`, improve the existing role level,
meet the configured confidence floor, quote evidence found in the listing, and cite retrieved
context IDs. It cannot change hard blockers, geography, eligibility, or demote relevance.
Malformed responses, unavailable model/index, invalid tools or arguments, excess rounds, missing
citations, low confidence, and timeouts retain the structured/embedding assessment with typed
fallback state. A successful promotion is rescored by the existing deterministic scorer.

Session 27 should compare these providers through the unchanged gold JSONL contract before any
operational reintegration.
