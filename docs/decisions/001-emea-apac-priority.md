# Decision 021 — EMEA and APAC regional priority

- Status: Approved
- Date: 2026-08-12
- Amends: Project Charter v1.0, sections 8 and 47

## Decision

EMEA and APAC are both primary target regions for Internship Monitor. The named markets in the
foundational charter remain preferred markets within those regions, while relevant opportunities
elsewhere in EMEA or APAC may be discovered when they come from an explicitly approved company.

Opportunities outside EMEA and APAC may still be discovered for approved companies, including
international remote roles, but they receive lower location priority unless a future configuration
or approved decision changes that behavior.

## Guardrails

- Regional priority affects discovery and relevance scoring only.
- Region membership never implies work authorization.
- EMEA-only, APAC-only, and vaguely remote listings require geographic eligibility analysis.
- Ambiguity produces an explainable warning rather than silent rejection.
- The company allowlist does not expand automatically.
- Region and market preferences must remain user-configurable.

## Consequences for implementation

Configuration models must support region-level and market-level preferences separately. Location
analysis must preserve evidence from the listing and must not collapse EMEA or APAC into a legal
eligibility outcome.

