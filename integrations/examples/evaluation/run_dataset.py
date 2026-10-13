"""Validate an evaluation dataset, see what a run would do, and run it against a model gateway.

Nothing here scores "accuracy". Every result comes from checks you wrote into the dataset, so a
"fail" means "the output matched none of the forms you listed". See docs/evaluation.md.

Plan a run without calling any model (always safe, needs no gateway):

    python run_dataset.py --starter starter-nigerian-languages --dry-run
    python run_dataset.py --dataset my-dataset.json --dry-run

Run it. The gateway is a factory you provide: ``package.module:function`` returning an object with
``async generate(request)`` (the ModelGateway port in forge_integrations.ports). It is the same kind
of factory the MCP server takes in MCP_GATEWAY_FACTORY, set here as EVAL_GATEWAY_FACTORY:

    $env:EVAL_GATEWAY_FACTORY = "my_platform.gateway:make_gateway"
    python run_dataset.py --starter starter-general-checks --out results

Writes results/<run id>.json and results/<run id>.csv (spreadsheet formulas in outputs are
neutralised in the CSV) and prints the summary. Exit codes: 0 success, 1 failure, 2 bad usage.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from forge_integrations.errors import ConfigError, ForgeError, ValidationFailed
from forge_integrations.evaluation import (
    EvaluationRunner,
    RunConfig,
    load_starter_dataset,
    starter_dataset_ids,
    validate_dataset,
)
from forge_integrations.evaluation.export import export_csv, export_json

_FACTORY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$")
MAX_DATASET_BYTES = 2 * 1024 * 1024


def load_gateway(spec: str | None) -> Any:
    if not spec:
        raise ConfigError("Set EVAL_GATEWAY_FACTORY to package.module:function to run against a model, or use --dry-run.")
    if not _FACTORY_RE.fullmatch(spec):
        raise ConfigError("EVAL_GATEWAY_FACTORY must look like package.module:function.")
    module_name, _, attribute = spec.partition(":")
    try:
        factory = getattr(importlib.import_module(module_name), attribute)
        gateway = factory()
    except Exception as exc:  # noqa: BLE001 - only the type is shown: the message may hold connection details
        raise ConfigError(f"EVAL_GATEWAY_FACTORY failed ({type(exc).__name__}).") from None
    if not callable(getattr(gateway, "generate", None)):
        raise ConfigError("The gateway must provide an async generate(request) method.")
    return gateway


def load_dataset(args: argparse.Namespace):
    if args.starter:
        return load_starter_dataset(args.starter)
    path = Path(args.dataset)
    try:
        if path.stat().st_size > MAX_DATASET_BYTES:
            raise ConfigError("The dataset file is larger than 2 MiB.")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise ConfigError(f"cannot read {path.name}.") from None
    except ValueError:
        raise ConfigError(f"{path.name} is not valid JSON.") from None
    return validate_dataset(raw)


def describe(dataset) -> dict[str, Any]:
    check_types = Counter(check.type for case in dataset.cases for check in case.checks)
    return {
        "dataset": dataset.name,
        "version": dataset.version,
        "cases": len(dataset.cases),
        "model_calls": sum(case.repeat for case in dataset.cases),
        "languages": sorted({case.language for case in dataset.cases if case.language}),
        "checks": dict(sorted(check_types.items())),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and run an evaluation dataset.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--starter", choices=starter_dataset_ids(), help="a dataset that ships with the package")
    source.add_argument("--dataset", help="path to your own dataset JSON file")
    parser.add_argument("--dry-run", action="store_true", help="validate and show the plan; call no model")
    parser.add_argument("--out", default="results", help="folder for the result files (default: results)")
    parser.add_argument("--concurrency", type=int, default=2, choices=range(1, 9), help="calls in flight at once (default 2)")
    parser.add_argument("--prompt-version", help="a label so you can tell runs apart")
    return parser


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    environment = os.environ if env is None else env
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        dataset = load_dataset(args)
    except ValidationFailed as exc:
        print("error: the dataset is not valid:", file=err)
        for issue in exc.issues:
            print(f"  {issue.path or '(dataset)'}: {issue.message}", file=err)
        return 2
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except ForgeError as exc:
        print(f"error: {exc.message}", file=err)
        return 2

    plan = describe(dataset)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2), file=out)
        print("Dry run: no model was called.", file=err)
        return 0

    try:
        gateway = load_gateway(environment.get("EVAL_GATEWAY_FACTORY"))
        runner = EvaluationRunner(gateway)
        config = RunConfig(max_concurrency=args.concurrency, prompt_version=args.prompt_version)
        run = asyncio.run(runner.run(dataset, config))
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2
    except ForgeError as exc:
        print(f"error: {exc.code}: {exc.message}", file=err)
        return 1

    folder = Path(args.out)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{run.run_id}.json").write_text(export_json(run), encoding="utf-8")
    (folder / f"{run.run_id}.csv").write_text(export_csv(run), encoding="utf-8", newline="")
    summary = run.summary
    print(json.dumps({"run_id": run.run_id, "status": run.status, "pass_rate": summary.get("pass_rate"), "counts": summary.get("counts"), "latency_ms": summary.get("latency_ms")}, indent=2), file=out)
    print("pass_rate is the share of checks that held. It is not model accuracy.", file=err)
    print(f"Wrote {folder / (run.run_id + '.json')} and {folder / (run.run_id + '.csv')}", file=err)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
