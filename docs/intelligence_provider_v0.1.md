# Intelligence provider boundary v0.1

Session 22 introduces an optional, local-only intelligence-provider boundary. It does not change deterministic assessment, opportunity strength, alert policy, evaluation outcomes, source adapters, or notification delivery.

## Configuration

`SearchConfiguration.intelligence` is disabled by default. The only v0.1 provider is `ollama`; its endpoint must be a local HTTP origin (`127.0.0.1`, `::1`, or `localhost`) with no path or credentials. The public example uses `http://127.0.0.1:11434` and a bounded health timeout.

Keep any user-specific profile changes in ignored `config.local/profile.yaml`. Enabling this section only permits an explicit health diagnostic; it does not make model requests or alter monitor runs.

## Health command

```bash
uv run internship-monitor intelligence-status --profile config.local/profile.yaml
uv run internship-monitor intelligence-status --profile config.local/profile.yaml --json
```

When disabled, the command reports `disabled` and makes no request. When enabled, it calls Ollama's documented local `GET /api/version` and `GET /api/tags` endpoints to report service availability, version, and installed model names. An enabled but unreachable or malformed response reports `unavailable` and returns exit status 1. No inference endpoint is called.

## Extension invariant

`IntelligenceProvider` currently exposes only `health() -> ProviderHealth`. Later embedding and structured-assessor providers may add evaluated capabilities behind this package, but must not bypass deterministic hard blockers or modify ranking until their benchmark evidence is accepted.
