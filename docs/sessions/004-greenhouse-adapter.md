# Session 4 - Greenhouse adapter

**Status:** Complete
**Date:** 2026-08-12

## What changed

We connected the project to its first real job-board format: Greenhouse. The adapter knows how to
ask Greenhouse's public jobs endpoint for full descriptions, read the returned job data, and turn
each job into the common format used by the rest of the project.

It checks that a company is enabled, is actually configured as Greenhouse, and has a board token
before making a request. It keeps the provider-specific details inside this one adapter. Greenhouse
HTML descriptions become clean readable text, job IDs are kept stable, application links and
locations are preserved, and the time the monitor found a listing is recorded.

The adapter does not call the real internet during tests. The tests use a fake Greenhouse response
and prove the exact public URL, description cleanup, missing-location handling, invalid response
handling, and HTTP failure behavior.

## Why this matters

The monitor can now retrieve raw listings from an approved Greenhouse company board and put them
into one reliable internal shape. This is the first working discovery connection. The runner from
Session 3 will safely isolate a Greenhouse outage so other company sources can still run.

## What the project can do now

- Read private or example company configuration.
- Represent every listing with the same validated fields.
- Connect an enabled Greenhouse board token to Greenhouse's public jobs API.
- Turn returned Greenhouse listings into canonical jobs without storing provider-only fields.
- Keep one source failure from stopping other sources.

## What is still not built

- Deciding whether a job is relevant to your internship search.
- Ranking EMEA/APAC locations, graduation fit, or work-authorization risk.
- Saving seen jobs, alerting you, daily digests, or scheduled runs.

## Next session

Session 5 should add deterministic role filtering and classification using the configurable role
preferences and the title/description evidence now available from Greenhouse.
