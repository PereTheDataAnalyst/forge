"""Sandbox-only static checks: undefined names and first-party import consistency."""
import ast, builtins, pathlib, symtable, sys

ROOT = pathlib.Path(sys.argv[1])            # backend directory
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__", "__builtins__"}
problems: list[str] = []

def top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    def visit(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name): names.add(n.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name): names.add(node.target.id)
            elif isinstance(node, ast.Import):
                for a in node.names: names.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names: names.add(a.asname or a.name)
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for attr in ("body", "orelse", "finalbody"): visit(getattr(node, attr, []) or [])
                for h in getattr(node, "handlers", []): visit(h.body)
    visit(tree.body); return names

def resolve_module(dotted: str):
    parts = dotted.split(".")
    base = ROOT.joinpath(*parts)
    if base.with_suffix(".py").exists(): return base.with_suffix(".py")
    if (base / "__init__.py").exists(): return base / "__init__.py"
    return None

def check_symtable(path: pathlib.Path, src: str):
    top = symtable.symtable(src, str(path), "exec")
    module_names = {s.get_name() for s in top.get_symbols() if s.is_assigned() or s.is_imported() or s.is_namespace()}
    def walk(table):
        for sym in table.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced(): continue
            if table.get_type() == "module":
                if not (sym.is_assigned() or sym.is_imported() or sym.is_namespace() or name in BUILTINS):
                    problems.append(f"{path.relative_to(ROOT)}: undefined name '{name}' at module level")
            elif sym.is_global() and name not in module_names and name not in BUILTINS:
                problems.append(f"{path.relative_to(ROOT)}: undefined name '{name}' in scope '{table.get_name()}'")
        for child in table.get_children(): walk(child)
    walk(top)

def check_imports(path: pathlib.Path, tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and node.module.split(".")[0] in ("app", "tests"):
            target = resolve_module(node.module)
            if target is None:
                problems.append(f"{path.relative_to(ROOT)}: cannot resolve module '{node.module}'"); continue
            defined = top_level_names(ast.parse(target.read_text()))
            for alias in node.names:
                if alias.name in defined: continue
                if resolve_module(f"{node.module}.{alias.name}") is not None: continue
                problems.append(f"{path.relative_to(ROOT)}: '{alias.name}' is not defined in '{node.module}'")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "app" and resolve_module(alias.name) is None:
                    problems.append(f"{path.relative_to(ROOT)}: cannot resolve module '{alias.name}'")

count = 0
for path in sorted(ROOT.rglob("*.py")):
    if "__pycache__" in path.parts: continue
    src = path.read_text(); tree = ast.parse(src); count += 1
    check_symtable(path, src); check_imports(path, tree)
print(f"checked {count} files")
for p in problems: print("PROBLEM:", p)
print("RESULT:", "OK" if not problems else f"{len(problems)} problem(s)")
sys.exit(1 if problems else 0)
