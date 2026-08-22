# Session 29.1 — verified source catalog expansion

## Canonical shared catalog

`config/source_catalog.yaml` is now the canonical, version-controlled shared catalog. It contains
only public source facts and no profile, authorization, notification, or AI configuration.
`config.local/source_catalog.yaml` remains a private compatibility snapshot of the original 16
records, but is not a second canonical catalog; new catalog work belongs in the shared path.

The first 16 records in the shared catalog are structurally identical to the private 29.0 catalog.
New records use the deterministic catalog-ID convention:

```text
greenhouse:<lowercase-board-id>
lever:<lowercase-board-id>
ashby:<lowercase-board-id>
```

Existing IDs were not renamed because catalog identity is intentionally separate from durable listing
state identity.

## Verification workflow

`catalog-verify` is a read-only, structured-source-only verifier:

```bash
uv run internship-monitor catalog-verify \
  --catalog config/source_catalog.yaml \
  --report docs/catalog_verification_2026-08-22.json
```

It validates catalog/provider identifiers through normal config loading, fetches only the currently
supported Greenhouse, Lever, or Ashby endpoints, normalizes with the production adapter contract,
and writes an optional safe report. The report contains employer/provider/board ID, result, listing
count, internship-signal count, up to three location examples, failure category, duration, and
verification timestamp. It deliberately excludes descriptions, state, notification queues, delivery,
Ollama, and AI.

Candidate discovery used a bounded public internship board list and a curated board index only as
research inputs. Those lists are never ingestion sources. Each selected board was independently
probed through its provider endpoint before promotion.

## Results

The candidate pass researched 175 unique structured boards:

| Provider | Verified + enabled | Candidate + disabled | Total |
| --- | ---:| ---:| ---:|
| Greenhouse | 63 | 14 | 77 |
| Lever | 12 | 20 | 32 |
| Ashby | 56 | 10 | 66 |
| **Total** | **131** | **44** | **175** |

No source was promoted based on an internship being currently open. 79 verified employers currently
show at least one internship-language signal; this is diagnostic only. The two pre-existing malformed
Lever boards (Spotify and Xsolla) remain verified/enabled and are expected to stay failure-isolated
at monitoring time. 44 newly researched boards remain `candidate` and disabled after malformed,
404, timeout, or uncertain structured responses. No board was retired solely from this one probe.

The requested Ashby seeds Airwallex, Cohere, Deliveroo, Notion, Weaviate, and Bloxd validated.
Thought Machine, Venti Technologies, and Deductive AI did not validate through their proposed board
identifiers and remain disabled candidates rather than being silently substituted.

A primary-category review of the 131 verified sources yields 7 large-tech/enterprise, 22 AI/ML,
32 developer-tools/cloud/data, 36 fintech/trading, 20 consumer/product, 6
robotics/autonomy/industrial, and 8 other technical sources. No consulting/professional-services
board was added merely to fill a category. Current public location
examples include the United States, Canada, United Kingdom, Ireland, Netherlands, Germany, France,
Portugal, Spain, Belgium, Singapore, Hong Kong, Japan, India, Malaysia, and Australia. They are
source observations, not user eligibility or sponsorship claims.

## Scale decision

The read-only expanded ingestion proof processed 131 source runs in 27.522 seconds under a global
limit of 16 and a provider-host limit of 6. It yielded 17,439 normalized listings; 129 snapshots were
authoritative and two pre-existing Lever sources failed in isolation. Assessing the 755
internship-language listings produced 121 role-relevant internship candidates.

A full unfiltered deterministic monitor run across every one of the 17,439 listings did not complete
within a bounded 180 seconds. The source-fetch layer is therefore healthy at this scale, but the
current every-listing assessment path needs batching before this catalog replaces a 30-minute hosted
cycle. Do not change the current workflow cadence automatically: its existing private catalog remains
independent. For a future 100–250-source shared production catalog, begin with provider/source cohorts
or a hot/cold cadence (for example, high-change sources more often and the complete catalog every
2–4 hours), then measure full-run CPU and source-health behavior.

The observed source-only duration supports rough network estimates of about 21 seconds for 100,
31 seconds for 150, and 53 seconds for 250 similarly distributed healthy sources. They are not
full-monitor estimates; the full assessment timeout above is the binding operational result.

## Next local shadow checkpoint

Do not run it in Actions. After reviewing the expanded dry-run result, run exactly one bounded local
shadow pass:

```bash
uv run internship-monitor run \
  --profile config.local/profile.yaml \
  --catalog config/source_catalog.yaml \
  --shadow-intelligence \
  --shadow-limit 3
```

Report considered, selected, attempted, deduplicated, succeeded, policy rejections, RAG retrievals,
tool calls, disagreements, and review-candidate count. If it attempts zero cases, report that once and
do not retry in a loop.
