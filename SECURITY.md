# Security Policy

## Supported versions

The latest `0.2.x` release receives security fixes. Older releases are not maintained.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:

https://github.com/ZeyuChang0471/marvel/security/advisories/new

Do not open a public issue for a security problem.

Please include:

- the affected version or commit;
- impact and reproduction steps;
- relevant logs with API keys, account identifiers and personal data removed.

## Scope

- API-key handling and accidental disclosure;
- the Web UI (it has no login and must stay bound to `127.0.0.1` unless behind an authenticating proxy);
- untrusted content returned by data sources;
- dependency vulnerabilities and unsafe deserialization.

## Safe harbour

Good-faith research that follows this policy and avoids privacy violations, service disruption or data destruction will not be pursued legally.
