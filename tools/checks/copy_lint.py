"""Repository copy lint. Usage: python tools/checks/copy_lint.py [repo_root]

Fails on: em dashes, banned marketing phrases, emoji, the obsolete company name, and non-ASCII
characters in PowerShell scripts. Standard library only.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[2]
BANNED = ["AI-powered", "AI-first", "revolutionary", "next-generation", "cutting-edge",
          "intelligent AI", "powered by artificial intelligence", "game-changing", "seamless"]
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2705\u274C]")
OLD_NAME = re.compile("prostack", re.IGNORECASE)
EXTENSIONS = {".ts", ".tsx", ".css", ".py", ".md", ".json", ".html", ".yml", ".yaml", ".ps1", ".toml", ".ini", ".txt", ".mako"}
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "dist", ".forge-backups"}
# This file names the banned items on purpose.
SELF = pathlib.Path(__file__).resolve()

problems = []
count = 0
for path in ROOT.rglob("*"):
    if not path.is_file() or any(part in SKIP_DIRS for part in path.parts) or path.resolve() == SELF:
        continue
    if path.suffix not in EXTENSIONS and path.name not in (".env.example", ".gitignore", ".gitattributes", ".editorconfig"):
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        problems.append(f"{path.relative_to(ROOT)}: not valid UTF-8")
        continue
    count += 1
    rel = path.relative_to(ROOT)
    # Test files may contain the character as an assertion input, for example a check that tool
    # descriptions have none. Product copy and documentation never may.
    if "\u2014" in text and "tests" not in path.relative_to(ROOT).parts:
        problems.append(f"{rel}: em dash")
    if EMOJI.search(text):
        problems.append(f"{rel}: emoji")
    if OLD_NAME.search(text):
        problems.append(f"{rel}: obsolete company name")
    if path.suffix != ".py":
        for phrase in BANNED:
            if phrase.lower() in text.lower():
                problems.append(f"{rel}: banned phrase '{phrase}'")
    if path.suffix == ".ps1":
        try:
            text.encode("ascii")
        except UnicodeEncodeError:
            problems.append(f"{rel}: non-ASCII character in a PowerShell script")
print(f"scanned {count} files")
for problem in problems:
    print("PROBLEM:", problem)
print("RESULT:", "OK" if not problems else f"{len(problems)} problem(s)")
sys.exit(1 if problems else 0)
