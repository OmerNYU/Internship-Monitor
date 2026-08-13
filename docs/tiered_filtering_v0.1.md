# Tiered filtering v0.1

Session 20 separates deterministic internship evaluation into three explicit dimensions:

1. **Hard blockers** are only proven incompatibilities. `JobAssessment.hard_blockers` holds immutable typed reasons and evidence. Unknown location, remote scope, sponsorship, language, graduation, or season evidence never creates one.
2. **Geographic routing** is represented by `LocationAssessment.geographic_bucket`: `priority_market`, `preferred_region`, `international_remote`, `stretch_region`, `manual_location_review`, or `blocked`.
3. **Strength and score** rank retained opportunities. The legacy score and recommendation remain available for ordering and alert timing; `OpportunityStrength` is a separate attention tier. A low score is routed to manual review or digest and is never a hard blocker by itself.

## Location evidence and multi-location semantics

Adapters retain the canonical location text. The analysis layer splits conventional canonical multi-location separators (`|`, semicolon, and line break) into `LocationCandidate` values. Each candidate records raw evidence, city/country/region when recognized, modality, and whether the configured `hard_excluded_countries` policy applies.

The opportunity is geographically blocked only when every known location candidate is explicitly hard-excluded. If an opportunity has a valid option (`Singapore | Bengaluru, India`), it remains actionable and the excluded option remains structured evidence. If it combines an excluded option with unresolved remote or unknown geography (`Bengaluru, India | Remote`), it is retained for manual location review.

## Routing rules

- A configured preferred city/country is a priority market.
- A recognized country in a configured primary region is a preferred region.
- Explicitly worldwide/international remote evidence is international remote.
- A recognized country outside primary regions is stretch, not rejected. This includes US roles with unknown sponsorship.
- Ordinary remote and unrecognized locations are manual location review.
- `blocked` is reserved for all-location hard exclusion; it does not infer authorization.

## Blocker and delivery relationship

Season, graduation, explicit authorization restrictions, mandatory unsupported language, explicit non-student/excluded role evidence, and all-option configured location exclusion are centralized in `hard_blockers`. Alert policy consumes those typed blockers: configured-location, incompatible-season, and clearly non-student role blockers suppress operational delivery; the other blockers remain digest-only for explainability and review compatibility. Existing score thresholds still control immediate vs delayed vs digest delivery only after a listing is not suppressed.

## Future-intelligence invariants

Future embedding, LLM, RAG, and adjudication layers may enrich role relevance and ordering, but they must not override explicit deterministic hard blockers, erase unresolved evidence, infer authorization from region, or turn a weak score into deletion.

Important examples:

- India-only with `India` in private `hard_excluded_countries`: blocked.
- Singapore plus Bangalore: retained as priority market; Bangalore is excluded evidence.
- France: preferred region, not suppressed for being outside a preferred city list.
- United States with no sponsorship statement: stretch region and authorization verification.
- Remote with unclear scope: manual location review, not blocked.
