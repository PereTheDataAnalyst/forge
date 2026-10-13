"""Command line for the Forge MCP server.

    python -m forge_integrations.mcp check
    python -m forge_integrations.mcp issue-token --project proj_123 --name "Claude Code" --scopes models:read,generate
    python -m forge_integrations.mcp list-tokens --project proj_123
    python -m forge_integrations.mcp revoke-token --project proj_123 --client-id <client id>
    python -m forge_integrations.mcp serve

Everything the server needs comes from environment variables (see docs/mcp.md); flags only cover
what changes from run to run. Secrets are never accepted as flags, because command lines are
visible to other users of the machine and end up in shell history.

Output contract for scripting: ``issue-token`` writes the token alone to standard output and every
explanation to standard error, so ``$token = python -m forge_integrations.mcp issue-token ...``
captures exactly the token. Exit codes: 0 success, 1 operational failure, 2 bad usage or configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import ipaddress
import json
import logging
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from .. import __version__
from ..config import McpSettings, load_mcp_settings, load_voice_settings
from ..errors import ConfigError, ForgeError, ValidationFailed
from ..observability import JsonLogObserver
from ..security.tokens import TokenHasher
from ..voice.asr_client import HttpAsrProvider
from .audit import AuditRecorder, JsonlAuditSink
from .deps import McpDependencies
from .policy import McpPolicy, StaticPolicyProvider
from .ratelimit import InMemoryRateLimiter
from .scopes import ALL_SCOPES, normalize_scopes
from .service import PROJECT_ID_RE, McpTokenService
from .stores import SqliteMcpClientStore

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PATH = "/mcp"
DEFAULT_STORE = "data/mcp-clients.sqlite3"
LOG_LEVELS = ("debug", "info", "warning", "error")
POLICY_FILE_MAX_BYTES = 256 * 1024

_FACTORY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,100}$")
_LOGGER_NAME = "forge.integrations"


class CliError(Exception):
    """A problem to report to the operator without a traceback."""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# --------------------------------------------------------------------------- helpers
def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def load_factory(spec: str, *, what: str) -> Any:
    """Import ``package.module:function`` and call it with no arguments.

    The factory is operator-supplied code, trusted exactly like the program itself. Only the exception
    type is reported when it fails, because the message may contain connection details.
    """
    if not _FACTORY_RE.fullmatch(spec or ""):
        raise CliError(f"{what} must look like package.module:function (got {spec!r}).")
    module_name, _, attribute = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        raise CliError(f"{what}: could not import {module_name!r} ({type(exc).__name__}).", EXIT_FAILED) from None
    target = getattr(module, attribute, None)
    if not callable(target):
        raise CliError(f"{what}: {spec} is not a callable.", EXIT_FAILED)
    try:
        return target()
    except Exception as exc:  # noqa: BLE001
        raise CliError(f"{what}: {spec} raised {type(exc).__name__} while starting.", EXIT_FAILED) from None


def _require_method(obj: Any, method: str, what: str) -> Any:
    if not callable(getattr(obj, method, None)):
        raise CliError(f"{what} must provide an async {method}() method.", EXIT_FAILED)
    return obj


def load_policy_file(path: str | os.PathLike[str]) -> StaticPolicyProvider:
    """Per-project MCP policy from a JSON file.

        {"default": {"enabled": true, "allowed_scopes": ["models:read", "project:read"]},
         "projects": {"proj_123": {"enabled": false}}}

    Unknown keys are errors, so a typo cannot silently widen access. Read once at start-up.
    """
    file = Path(path)
    try:
        size = file.stat().st_size
    except OSError:
        raise CliError(f"Policy file {str(file)!r} cannot be read.") from None
    if size > POLICY_FILE_MAX_BYTES:
        raise CliError(f"Policy file {str(file)!r} is larger than {POLICY_FILE_MAX_BYTES} bytes.")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"Policy file {str(file)!r} is not valid JSON ({type(exc).__name__}).") from None
    if not isinstance(data, dict) or not set(data) <= {"default", "projects"}:
        raise CliError('Policy file must be an object with only "default" and "projects".')

    def parse(value: Any, where: str) -> McpPolicy:
        if not isinstance(value, dict) or not set(value) <= {"enabled", "allowed_scopes"}:
            raise CliError(f'Policy file: {where} must be an object with only "enabled" and "allowed_scopes".')
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CliError(f"Policy file: {where}.enabled must be true or false.")
        scopes = ALL_SCOPES
        if "allowed_scopes" in value:
            raw = value["allowed_scopes"]
            if not isinstance(raw, list):
                raise CliError(f"Policy file: {where}.allowed_scopes must be a list.")
            try:
                scopes = normalize_scopes(raw, path=f"{where}.allowed_scopes", allow_empty=True)
            except ValidationFailed as exc:
                raise CliError("Policy file: " + "; ".join(f"{i.path}: {i.message}" for i in exc.issues)) from None
        return McpPolicy(enabled=enabled, allowed_scopes=scopes)

    default = parse(data["default"], "default") if "default" in data else McpPolicy()
    projects: dict[str, McpPolicy] = {}
    raw_projects = data.get("projects", {})
    if not isinstance(raw_projects, dict):
        raise CliError('Policy file: "projects" must be an object.')
    for project_id, value in raw_projects.items():
        if not PROJECT_ID_RE.match(project_id):
            raise CliError(f"Policy file: {project_id[:40]!r} is not a valid project id.")
        projects[project_id] = parse(value, f"projects.{project_id}")
    return StaticPolicyProvider(default, projects)


def configure_logging(level: str) -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    if not any(getattr(handler, "_forge_cli", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._forge_cli = True  # type: ignore[attr-defined]
        logger.addHandler(handler)


@dataclass
class Assembly:
    """Everything built from the environment: ready to serve, and enough to explain itself."""

    settings: McpSettings
    deps: McpDependencies
    store: SqliteMcpClientStore
    store_path: str
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def close(self) -> None:
        self.store.close()


def _store_path(args: argparse.Namespace, env: Mapping[str, str]) -> str:
    return getattr(args, "store", None) or env.get("MCP_STORE_PATH") or DEFAULT_STORE


def _hasher(settings: McpSettings) -> TokenHasher:
    if not settings.token_pepper:
        raise CliError(
            "MCP_TOKEN_PEPPER is not set. Create one and keep it in your secret manager:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            "Losing it invalidates every issued token; changing it does too."
        )
    return TokenHasher(settings.token_pepper)  # raises ConfigError when shorter than 32 bytes


def assemble(args: argparse.Namespace, env: Mapping[str, str]) -> Assembly:
    settings = load_mcp_settings(env)
    hasher = _hasher(settings)
    notes: list[str] = []
    warnings: list[str] = []

    store_path = _store_path(args, env)
    store = SqliteMcpClientStore(store_path)
    try:
        observer = JsonLogObserver(logging.getLogger(_LOGGER_NAME))
        audit_file = getattr(args, "audit_file", None) or env.get("MCP_AUDIT_FILE")
        sink = JsonlAuditSink(audit_file) if audit_file else None
        notes.append(f"audit log ........... {audit_file} (JSON lines, owner-only)" if sink else "audit log ........... log stream only (set MCP_AUDIT_FILE to keep a file)")
        audit = AuditRecorder(sink, observer)
        tokens = McpTokenService(store, hasher, settings, audit=audit)

        limiter_spec = env.get("MCP_RATE_LIMITER_FACTORY")
        if limiter_spec:
            limiter = _require_method(load_factory(limiter_spec, what="MCP_RATE_LIMITER_FACTORY"), "check", "The rate limiter")
            notes.append(f"rate limiter ........ {limiter_spec}")
        else:
            limiter = InMemoryRateLimiter()
            notes.append("rate limiter ........ in this process only (run one worker; use MCP_RATE_LIMITER_FACTORY to share limits)")

        policy_file = getattr(args, "policy_file", None) or env.get("MCP_POLICY_FILE")
        if policy_file:
            policy = load_policy_file(policy_file)
            notes.append(f"project policy ...... {policy_file}")
        else:
            policy = StaticPolicyProvider()
            notes.append("project policy ...... default: enabled, every scope the token holds")

        gateway = None
        if env.get("MCP_GATEWAY_FACTORY"):
            gateway = _require_method(load_factory(env["MCP_GATEWAY_FACTORY"], what="MCP_GATEWAY_FACTORY"), "generate", "The model gateway")
            notes.append(f"model gateway ....... {env['MCP_GATEWAY_FACTORY']}")
        else:
            notes.append("model gateway ....... not configured (generate, chat and translate are hidden)")

        usage = None
        if env.get("MCP_USAGE_FACTORY"):
            usage = _require_method(load_factory(env["MCP_USAGE_FACTORY"], what="MCP_USAGE_FACTORY"), "summary", "The usage reader")
            notes.append(f"usage reader ........ {env['MCP_USAGE_FACTORY']}")
        projects = None
        if env.get("MCP_PROJECTS_FACTORY"):
            projects = _require_method(load_factory(env["MCP_PROJECTS_FACTORY"], what="MCP_PROJECTS_FACTORY"), "get", "The project provider")
            notes.append(f"project provider .... {env['MCP_PROJECTS_FACTORY']}")

        voice = load_voice_settings(env)
        asr = None
        if voice.asr_service_url:
            asr = HttpAsrProvider(
                voice.asr_service_url,
                voice.asr_service_token,
                allowed_hosts=voice.asr_allowed_hosts,
                allow_private=voice.allow_private_targets,
                allow_insecure_http=voice.allow_insecure_http,
                timeout_seconds=voice.asr_timeout_seconds,
            )
            notes.append(f"speech recognition .. ASR service at {_origin_only(voice.asr_service_url)}")
            if voice.allow_private_targets:
                warnings.append("ALLOW_PRIVATE_NETWORK_TARGETS is on: the ASR service may be on a private address. Keep it off in production unless the worker is private by design.")
            if voice.allow_insecure_http:
                warnings.append("ALLOW_INSECURE_HTTP_TARGETS is on: audio goes to the ASR service without TLS. Use it only on a trusted local network.")
        else:
            notes.append("speech recognition .. not configured (the transcribe tool is hidden)")

        deps = McpDependencies(
            tokens=tokens,
            rate_limiter=limiter,
            audit=audit,
            policy=policy,
            observer=observer,
            gateway=gateway,
            asr=asr,
            usage=usage,
            projects=projects,
        )
    except BaseException:
        store.close()
        raise
    return Assembly(settings, deps, store, store_path, notes, warnings)


def _origin_only(url: str) -> str:
    """Scheme, host and port of a URL: enough to recognise it, never a path, query or credentials."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = parts.hostname or "?"
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


# --------------------------------------------------------------------------- the server
@dataclass(frozen=True)
class ServeOptions:
    host: str
    port: int
    path: str
    log_level: str
    ssl_certfile: str | None
    ssl_keyfile: str | None
    forwarded_allow_ips: str | None
    max_concurrent_requests: int


def run_server(app: Any, options: ServeOptions) -> None:
    """Run the application with uvicorn. Separate so tests can replace it."""
    try:
        import uvicorn
    except ImportError:
        raise CliError("uvicorn is not installed. Install the server extra:  pip install \".[mcp]\"", EXIT_FAILED) from None
    config = uvicorn.Config(
        app,
        host=options.host,
        port=options.port,
        log_level=options.log_level,
        access_log=False,  # request lines can carry query strings; the audit log covers what matters
        server_header=False,
        lifespan="on",
        timeout_keep_alive=5,
        # The application sheds load itself with a JSON 503; this is the backstop beneath it.
        limit_concurrency=options.max_concurrent_requests + 100,
        h11_max_incomplete_event_size=16 * 1024,
        ssl_certfile=options.ssl_certfile,
        ssl_keyfile=options.ssl_keyfile,
        forwarded_allow_ips=options.forwarded_allow_ips,
    )
    uvicorn.Server(config).run()


def _serve(args: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    host = args.host or env.get("MCP_HOST") or DEFAULT_HOST
    try:
        port = int(args.port if args.port is not None else env.get("MCP_PORT", DEFAULT_PORT))
    except ValueError:
        raise CliError("MCP_PORT must be a whole number.") from None
    if not 1 <= port <= 65535:
        raise CliError("The port must be between 1 and 65535.")
    path = args.path or env.get("MCP_PATH") or DEFAULT_PATH
    if not _PATH_RE.fullmatch(path) or len(path) < 2 or path.endswith("/") or ".." in path or path in ("/healthz", "/readyz"):
        raise CliError("The endpoint path must start with / and use only letters, digits and . _ ~ - /, with no trailing slash, for example /mcp.")
    log_level = (args.log_level or env.get("MCP_LOG_LEVEL") or "info").lower()
    if log_level not in LOG_LEVELS:
        raise CliError(f"The log level must be one of: {', '.join(LOG_LEVELS)}.")
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        raise CliError("Give both --ssl-certfile and --ssl-keyfile, or neither.")
    tls = bool(args.ssl_certfile)
    if not is_loopback(host) and not tls and not args.behind_tls_proxy:
        raise CliError(
            f"Refusing to listen on {host} without TLS: bearer tokens would cross the network in clear text.\n"
            "Terminate TLS in front of this server and pass --behind-tls-proxy, or pass --ssl-certfile and --ssl-keyfile."
        )

    configure_logging(log_level)
    assembly = assemble(args, env)
    try:
        from .server import create_mcp_app
        from .tools import McpToolbox

        for line in assembly.warnings:
            print(f"warning: {line}", file=err)
        if not is_loopback(host) and not assembly.settings.allowed_hosts:
            print(
                "warning: MCP_ALLOWED_HOSTS is not set, so only localhost, 127.0.0.1 and [::1] are accepted as the Host header. "
                "Set it to the public name clients use, for example MCP_ALLOWED_HOSTS=mcp.example.com.",
                file=err,
            )
        app = create_mcp_app(assembly.deps, assembly.settings, path=path)
        offered = [spec.name for spec in McpToolbox(assembly.deps, assembly.settings).visible_specs(ALL_SCOPES)]
        assembly.deps.observer.emit(
            {
                "event": "mcp.server_starting",
                "version": __version__,
                "host": host,
                "port": port,
                "path": path,
                "tls": tls,
                "behind_tls_proxy": bool(args.behind_tls_proxy),
                "tools": offered,
                "allowed_hosts": list(assembly.settings.allowed_hosts) or ["localhost", "127.0.0.1", "[::1]"],
                "allowed_origins": list(assembly.settings.allowed_origins),
            }
        )
        run_server(
            app,
            ServeOptions(
                host=host,
                port=port,
                path=path,
                log_level=log_level,
                ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile,
                forwarded_allow_ips=args.forwarded_allow_ips or env.get("FORWARDED_ALLOW_IPS"),
                max_concurrent_requests=assembly.settings.max_concurrent_requests,
            ),
        )
    finally:
        assembly.close()
    return EXIT_OK


# --------------------------------------------------------------------------- admin commands
def _service(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[McpTokenService, SqliteMcpClientStore, McpSettings]:
    settings = load_mcp_settings(env)
    hasher = _hasher(settings)
    store = SqliteMcpClientStore(_store_path(args, env))
    audit_file = env.get("MCP_AUDIT_FILE")
    try:
        audit = AuditRecorder(JsonlAuditSink(audit_file) if audit_file else None)
    except BaseException:
        store.close()
        raise
    return McpTokenService(store, hasher, settings, audit=audit), store, settings


def _issue(args: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    scopes = [part.strip() for part in args.scopes.split(",")] if args.scopes is not None else None
    service, store, _ = _service(args, env)
    try:
        issued = asyncio.run(
            service.issue(project_id=args.project, name=args.name, scopes=scopes, ttl_days=args.ttl_days, actor="cli")
        )
    finally:
        store.close()
    record = issued.record
    if args.json:
        view = record.public_view()
        view["token"] = issued.token
        print(json.dumps(view, ensure_ascii=False, indent=2), file=out)
    else:
        print(f"Issued for project {record.project_id}, client {record.client_id}.", file=err)
        print(f"Scopes:  {', '.join(sorted(record.scopes))}", file=err)
        print(f"Expires: {record.expires_at.isoformat() if record.expires_at else 'never'}", file=err)
        print("This token is shown once and cannot be recovered. Put it in a secret manager now.", file=err)
        print(issued.token, file=out)
    return EXIT_OK


def _list(args: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    service, store, _ = _service(args, env)
    try:
        rows = asyncio.run(service.list_clients(args.project))
    finally:
        store.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2), file=out)
        return EXIT_OK
    if not rows:
        print(f"No MCP clients for project {args.project}.", file=out)
        return EXIT_OK
    header = ("CLIENT ID", "NAME", "PREFIX", "STATUS", "EXPIRES", "LAST USED", "SCOPES")
    table = [header]
    for row in rows:
        status = "revoked" if row["revoked_at"] else "active"
        table.append(
            (
                row["client_id"],
                row["name"],
                row["token_prefix"],
                status,
                (row["expires_at"] or "never")[:10],
                (row["last_used_at"] or "never")[:19],
                ",".join(row["scopes"]),
            )
        )
    widths = [max(len(line[i]) for line in table) for i in range(len(header))]
    for line in table:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)).rstrip(), file=out)
    return EXIT_OK


def _revoke(args: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    service, store, _ = _service(args, env)
    try:
        changed = asyncio.run(service.revoke(args.project, args.client_id, actor="cli"))
    finally:
        store.close()
    print("Revoked. The token stops working on its next request." if changed else "That client was already revoked.", file=out)
    return EXIT_OK


def _check(args: argparse.Namespace, env: Mapping[str, str], out: TextIO, err: TextIO) -> int:
    from .tools import McpToolbox

    print("Forge MCP server configuration check", file=out)
    settings = load_mcp_settings(env)
    pepper = settings.token_pepper
    if not pepper:
        print("  token pepper ........ MISSING (set MCP_TOKEN_PEPPER)", file=out)
        print("\nNot ready: MCP_TOKEN_PEPPER is required.", file=out)
        return EXIT_FAILED
    print(f"  token pepper ........ set ({len(pepper.encode('utf-8'))} bytes)", file=out)
    assembly = assemble(args, env)
    try:
        s = assembly.settings
        print(f"  client store ........ {assembly.store_path}", file=out)
        print(f"  allowed hosts ....... {', '.join(s.allowed_hosts) if s.allowed_hosts else 'localhost, 127.0.0.1, [::1] (default)'}", file=out)
        print(f"  allowed origins ..... {', '.join(s.allowed_origins) if s.allowed_origins else 'none: browser clients are refused'}", file=out)
        for line in assembly.notes:
            print(f"  {line}", file=out)
        offered = [spec.name for spec in McpToolbox(assembly.deps, s).visible_specs(ALL_SCOPES)]
        print(f"  tools offered ....... {', '.join(offered)}", file=out)
        print(
            f"  limits .............. {s.rate_limit_generate_per_minute}/min generate, {s.rate_limit_transcribe_per_minute}/min transcribe, "
            f"{s.rate_limit_evaluate_per_minute}/min evaluate, {s.rate_limit_requests_per_minute}/min requests per token, "
            f"{s.rate_limit_project_per_minute}/min per project",
            file=out,
        )
        if not assembly.deps.gateway and not assembly.deps.asr:
            assembly.warnings.append("No model gateway and no ASR service are configured, so no tool can reach a model.")
        for line in assembly.warnings:
            print(f"  warning: {line}", file=out)
        print("\nReady.", file=out)
    finally:
        assembly.close()
    return EXIT_OK


# --------------------------------------------------------------------------- entry point
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge-mcp-server",
        description="Run the Forge MCP server and manage the tokens agents use to reach it. Configuration is read from environment variables.",
    )
    parser.add_argument("--version", action="version", version=f"forge-integrations {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--store", help=f"SQLite file holding MCP clients (env MCP_STORE_PATH, default {DEFAULT_STORE})")
    shared.add_argument("--debug", action="store_true", help="print a traceback for unexpected errors")

    serve = sub.add_parser("serve", parents=[shared], help="run the MCP server")
    serve.add_argument("--host", help=f"address to listen on (env MCP_HOST, default {DEFAULT_HOST})")
    serve.add_argument("--port", type=int, help=f"port (env MCP_PORT, default {DEFAULT_PORT})")
    serve.add_argument("--path", help=f"endpoint path (env MCP_PATH, default {DEFAULT_PATH})")
    serve.add_argument("--audit-file", help="append audit events to this JSON lines file (env MCP_AUDIT_FILE)")
    serve.add_argument("--policy-file", help="per-project policy JSON file (env MCP_POLICY_FILE)")
    serve.add_argument("--log-level", choices=LOG_LEVELS, help="env MCP_LOG_LEVEL, default info")
    serve.add_argument("--ssl-certfile", help="TLS certificate (PEM)")
    serve.add_argument("--ssl-keyfile", help="TLS private key (PEM)")
    serve.add_argument("--behind-tls-proxy", action="store_true", help="confirm TLS is terminated in front of this server (required to listen on a non-loopback address without --ssl-certfile)")
    serve.add_argument("--forwarded-allow-ips", help="proxy addresses whose X-Forwarded-For is trusted (env FORWARDED_ALLOW_IPS, default 127.0.0.1)")

    issue = sub.add_parser("issue-token", parents=[shared], help="create a scoped token for an agent or tool")
    issue.add_argument("--project", required=True, help="project id the token belongs to")
    issue.add_argument("--name", required=True, help="a label such as the tool and who uses it")
    issue.add_argument("--scopes", help=f"comma separated; default models:read,project:read. Available: {', '.join(sorted(ALL_SCOPES))}")
    issue.add_argument("--ttl-days", type=int, help="days until it expires (default MCP_TOKEN_DEFAULT_TTL_DAYS)")
    issue.add_argument("--json", action="store_true", help="print the full result as JSON on standard output")

    listing = sub.add_parser("list-tokens", parents=[shared], help="list a project's MCP clients (never shows secrets)")
    listing.add_argument("--project", required=True)
    listing.add_argument("--json", action="store_true")

    revoke = sub.add_parser("revoke-token", parents=[shared], help="revoke a client's token immediately")
    revoke.add_argument("--project", required=True)
    revoke.add_argument("--client-id", required=True)

    sub.add_parser("check", parents=[shared], help="validate configuration and show what would be served")
    return parser


_COMMANDS: dict[str, Callable[..., int]] = {
    "serve": _serve,
    "issue-token": _issue,
    "list-tokens": _list,
    "revoke-token": _revoke,
    "check": _check,
}


def _format_validation(exc: ValidationFailed) -> str:
    return "Invalid input:\n" + "\n".join(f"  {issue.path}: {issue.message}" for issue in exc.issues)


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    for stream in (out, err):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")  # a legacy console must not turn a name into a crash
            except Exception:  # noqa: BLE001
                pass
    environment = os.environ if env is None else env
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed its message
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    try:
        return _COMMANDS[args.command](args, environment, out, err)
    except CliError as exc:
        print(f"error: {exc.message}", file=err)
        return exc.code
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return EXIT_USAGE
    except ValidationFailed as exc:
        print(f"error: {_format_validation(exc)}", file=err)
        return EXIT_USAGE
    except ForgeError as exc:
        print(f"error: {exc.message}", file=err)
        return EXIT_FAILED
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        print(f"error: unexpected failure ({type(exc).__name__}). Run with --debug for details.", file=err)
        if getattr(args, "debug", False):
            traceback.print_exc(file=err)
        return EXIT_FAILED
