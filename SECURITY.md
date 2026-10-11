# Security policy

## Reporting a vulnerability

Please report security issues privately. Use GitHub's private vulnerability reporting (the Security tab of this repository, then "Report a vulnerability"). Do not open a public issue for a vulnerability and do not include real secrets or personal data in a report.

Include the affected version or commit, steps to reproduce, and the impact you observed. We aim to acknowledge reports promptly and to fix confirmed issues before disclosing them.

## Scope

In scope: the code in this repository, including the gateway, authentication, API keys, the inference worker and the web app. Out of scope: the N-ATLAS models and their Hugging Face repositories, and third-party services.

## Supported versions

Only the latest commit on `develop` is supported during the competition period.

## What exists

See [docs/security.md](docs/security.md) for the implemented controls and the known gaps. N-ATLAS Forge has not been independently audited and holds no security certifications.
