# Forge advanced integrations

Evaluation Lab engine, N-ATLAS speech (ASR) integration and the Forge MCP server for N-ATLAS Forge (2026 N-ATLAS Innovation Challenge, StackTechnology).

This package is built against small interfaces (ports) so the core platform plugs in its own gateway, database, rate limiter and authentication. It does not
replace or redesign any of them. The core code has no third-party dependencies (Python 3.10 or newer, standard library only). The MCP server and the ASR worker
bring their own extras.

## What is in it

| Area | Folder | What it does |
| --- | --- | --- |
| Evaluation Lab | `src/forge_integrations/evaluation` | Validates developer-written datasets, runs them against a model gateway, reports which checks held. No accuracy score, no model judge. Run manager, comparison, JSON and CSV export, two starter datasets. |
| Voice | `src/forge_integrations/voice` | Audio validation by signature, hardened ffmpeg decoding, HTTP client for the ASR worker, a pipeline with a step trail. |
| ASR worker | `src/forge_integrations/asr_service`, `asr-service/` | HTTP service that runs the four official N-ATLAS speech models. |
| MCP server | `src/forge_integrations/mcp` | Scoped, rate-limited, audited MCP server (8 tools) with a command line for tokens and serving. |
| Security | `src/forge_integrations/security` | Redaction, token hashing, host and origin checks, SSRF-safe outbound calls, safe temp files. |
| Facts | `src/forge_integrations/natlas_facts.py` | The N-ATLAS facts that were verified from the official model cards. |
| Docs | `docs/` | `mcp.md`, `evaluation.md`, `voice.md`, `security.md`, `integration-contract.md`, `n-atlas-verified-facts.md` |
| Examples | `examples/` | `mcp/` (standard library client, official SDK client, client config files), `voice/`, `evaluation/` |
| Tests | `tests/` | `unittest` suite, about 500 tests |

## Run the tests (Windows PowerShell)

The suite needs the MCP SDK for its MCP tests, so use a virtual environment with the `mcp` extra. ffmpeg is optional: tests that decode audio skip cleanly when it is missing.

```powershell
cd C:\Projects\forge\integrations
py -3 -m venv .venv-mcp
.\.venv-mcp\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[mcp,http]"
$env:PYTHONPATH = "src"
python -W error -m unittest discover -s tests -t .
```

The `-t .` is required (the tests use relative imports). Expect `OK` and no failures. The number of tests skipped depends on whether ffmpeg is installed.

## Start the MCP server in five commands

```powershell
$env:MCP_TOKEN_PEPPER = python -c "import secrets; print(secrets.token_urlsafe(48))"
python -m forge_integrations.mcp check
$env:FORGE_MCP_TOKEN = python -m forge_integrations.mcp issue-token --project demo-project --name "My laptop" --scopes models:read,project:read
python -m forge_integrations.mcp serve
# in a second window, with the same FORGE_MCP_TOKEN:  python examples\mcp\call_tools.py list
```

Without a model gateway only `get_model_info` and `get_project_info` appear. The platform supplies the gateway through `MCP_GATEWAY_FACTORY` (see `docs/mcp.md`).

## Honest status

Tested here: everything in `tests/`, which includes both MCP protocol revisions over a real socket, the official `mcp` 2.2.0 client over HTTP, the command line with a real server start, the ASR worker application with a test engine, the examples, and the security behaviours listed in `docs/security.md`.

Not tested here, and not to be described as working until someone has run it:

- Real N-ATLAS model inference (language model or speech). It needs the platform's gateway or N-ATLAS API credentials for text, and `torch`, `transformers` plus a Hugging Face token for the gated speech models. `asr-service/README.md` ends in a `selftest` that proves it.
- The editors' own MCP clients (Claude Code, Cursor, VS Code). Their documented config formats are in `examples/mcp/configs/`; Claude Desktop is not verified.
- A shared Redis or Memurai rate limiter, more than one server process, TLS in front of the servers.
- Database tables, API routes and dashboard screens: they belong to the core platform. `docs/integration-contract.md` has the proposed SQL and routes.
- No independent security review. No accuracy, word error rate or translation quality has been measured.

No SOC 2, ISO, HIPAA, GDPR or any other certification or compliance claim is made or implied.
