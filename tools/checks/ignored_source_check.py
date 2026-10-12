"""Fails when Git ignores files that look like source code.

A broad .gitignore rule can hide a real package without any warning. In this project the rule
`models/` (meant for model weights) also matched `backend/app/db/models/`, so those files never
reached GitHub. This check lists everything Git ignores, skips generated locations, and reports every
source-looking file that is hidden, together with the rule that hides it.

Run from anywhere inside the repository:

    python tools/checks/ignored_source_check.py

Exit status 0 means nothing suspicious is hidden. Exit status 1 lists the files and the rules.
"""
import os
import pathlib
import subprocess
import sys

SOURCE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".md", ".ps1",
    ".sql", ".ini", ".toml", ".mako", ".json", ".yml", ".yaml",
}
GENERATED_PARTS = {
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", "dist", "htmlcov", ".forge-backups", ".git",
}


def git(*arguments):
    result = subprocess.run(["git", *arguments], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        sys.exit("git " + " ".join(arguments) + " failed: " + result.stderr.strip())
    return result.stdout


def main():
    os.chdir(git("rev-parse", "--show-toplevel").strip())
    listing = git("ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    offenders = []
    for entry in (line for line in listing.splitlines() if line):
        path = pathlib.PurePosixPath(entry)
        if GENERATED_PARTS & set(path.parts):
            continue
        if entry.endswith("/"):
            for current, directories, files in os.walk(entry):
                directories[:] = [name for name in directories if name not in GENERATED_PARTS]
                for name in files:
                    if pathlib.PurePosixPath(name).suffix.lower() in SOURCE_SUFFIXES:
                        offenders.append(pathlib.PurePosixPath(current, name).as_posix())
        elif path.suffix.lower() in SOURCE_SUFFIXES:
            offenders.append(entry)

    if not offenders:
        print("ok: Git ignores no source-looking files")
        return 0

    print("Git ignores files that look like source code:")
    for path in sorted(set(offenders)):
        rule = git("check-ignore", "-v", path).strip().replace("\t", "  ") or "(rule not found)"
        print("  " + path)
        print("      " + rule)
    print()
    print("Anchor the responsible .gitignore rule with a leading slash, or narrow it, then run this check again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
