"""Sandbox-only static validation for PowerShell scripts (no interpreter available)."""
import pathlib, re, sys

PAIRS = {"{": "}", "(": ")", "[": "]"}
problems = []

def strip_strings_and_comments(text, name):
    """Remove comments and string literals so delimiter counting only sees code."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        two = text[i:i+2]
        if two == "<#":                                  # block comment
            j = text.find("#>", i + 2)
            if j < 0: problems.append(f"{name}: unterminated block comment"); return "".join(out)
            out.append("\n" * text[i:j].count("\n")); i = j + 2; continue
        if c == "#":                                     # line comment (a '#' inside code starts a comment)
            j = text.find("\n", i); j = n if j < 0 else j
            i = j; continue
        if two in ('@"', "@'") and re.match(r"[ \t]*\r?\n", text[i+2:i+6]):   # here-string
            quote = two[1]; j = text.find("\n" + quote + "@", i)
            if j < 0: problems.append(f"{name}: unterminated here-string"); return "".join(out)
            out.append("\n" * text[i:j].count("\n")); i = j + 3; continue
        if c == "'":                                     # single-quoted: '' is an escaped quote
            j = i + 1
            while j < n:
                if text[j] == "'" and text[j+1:j+2] == "'": j += 2; continue
                if text[j] == "'": break
                j += 1
            else:
                problems.append(f"{name}: unterminated single-quoted string near line {text[:i].count(chr(10))+1}"); return "".join(out)
            out.append("''"); i = j + 1; continue
        if c == '"':                                     # double-quoted: backtick escapes, "" escapes, $( ) nests
            j = i + 1; depth = 0
            while j < n:
                ch = text[j]
                if ch == "`": j += 2; continue
                if ch == "$" and text[j+1:j+2] == "(": depth += 1; j += 2; continue
                if depth and ch == ")": depth -= 1; j += 1; continue
                if ch == '"' and text[j+1:j+2] == '"' and not depth: j += 2; continue
                if ch == '"' and not depth: break
                j += 1
            else:
                problems.append(f"{name}: unterminated double-quoted string near line {text[:i].count(chr(10))+1}"); return "".join(out)
            out.append('""'); i = j + 1; continue
        out.append(c); i += 1
    return "".join(out)

def check_balance(code, name):
    stack = []
    line = 1
    for ch in code:
        if ch == "\n": line += 1
        if ch in PAIRS: stack.append((ch, line))
        elif ch in PAIRS.values():
            if not stack or PAIRS[stack[-1][0]] != ch:
                problems.append(f"{name}: unbalanced '{ch}' at line {line}"); return
            stack.pop()
    for ch, ln in stack: problems.append(f"{name}: unclosed '{ch}' opened at line {ln}")

FORBIDDEN = [(r"&&", "&& (PowerShell 7 only)"), (r"\|\|", "|| (PowerShell 7 only)"), (r"\?\?", "?? (PowerShell 7 only)"), (r"\?\.", "?. (PowerShell 7 only)"),
             (r"-SkipHttpErrorCheck", "-SkipHttpErrorCheck (PowerShell 7 only)"), (r"-Parallel\b", "-Parallel (PowerShell 7 only)"), (r"\$IsWindows\b", "$IsWindows (PowerShell 7 only)")]
files = sorted(pathlib.Path(sys.argv[1]).rglob("*.ps1"))
for path in files:
    raw = path.read_bytes()
    name = str(path.relative_to(sys.argv[1]))
    try: text = raw.decode("ascii")
    except UnicodeDecodeError as exc: problems.append(f"{name}: non-ASCII byte at offset {exc.start}"); continue
    if b"\r\n" in raw and b"\n" in raw.replace(b"\r\n", b""): problems.append(f"{name}: mixed line endings")
    code = strip_strings_and_comments(text, name)
    check_balance(code, name)
    for pattern, label in FORBIDDEN:
        if re.search(pattern, code): problems.append(f"{name}: uses {label}")
    for line_no, line in enumerate(code.split("\n"), 1):
        for match in re.finditer(r"Join-Path\b", line):
            rest, depth, token, tokens = line[match.end():], 0, "", []
            for ch in rest:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0: break
                    depth -= 1
                elif depth == 0 and ch in "|;": break
                if ch.isspace() and depth == 0:
                    if token: tokens.append(token); token = ""
                else: token += ch
            if token: tokens.append(token)
            positional, skip = 0, False
            for t in tokens:
                if skip: skip = False; continue
                if t.startswith("-"): skip = True; continue
                positional += 1
            if positional > 2: problems.append(f"{name}:{line_no}: Join-Path with {positional} positional values (fails on 5.1)")
    if "Set-StrictMode" in code: problems.append(f"{name}: Set-StrictMode could change behaviour on 5.1")
print(f"checked {len(files)} PowerShell files")
for p in problems: print("PROBLEM:", p)
print("RESULT:", "OK" if not problems else f"{len(problems)} problem(s)")
sys.exit(1 if problems else 0)
