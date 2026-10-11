"""Sandbox-only: compare ORM models with the hand-written Alembic migrations (AST based)."""
import ast, pathlib, re, sys

BACKEND = pathlib.Path(sys.argv[1])
problems: list[str] = []

def src(node): return ast.unparse(node)

def coarse_type(text: str) -> str:
    t = text.replace(" ", "")
    m = re.search(r"String\((?:length=)?(\d+)\)", t)
    if t.startswith("ARRAY(") or ".ARRAY(" in t:
        inner = re.search(r"String\((?:length=)?(\d+)\)", t)
        return f"ARRAY(String({inner.group(1)}))" if inner else "ARRAY"
    if m and "String(" in t.split("(")[0] + "(": return f"String({m.group(1)})"
    for token in ("JSONB", "Uuid", "DateTime", "Boolean", "SmallInteger", "BigInteger", "Integer", "Text", "Date"):
        if re.search(rf"\b{token}\b", t): return token
    return t

# ---------- models ----------
MODELS = {}   # table -> dict(columns={name: (type, nullable)}, fks={name: (col, ref, ondelete)}, uqs=set, ixs={name: cols}, pk=set)
for path in sorted((BACKEND / "app/db/models").glob("*.py")):
    tree = ast.parse(path.read_text())
    for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        table = next((s.value.value for s in cls.body if isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in s.targets)), None)
        if table is None: continue
        t = MODELS.setdefault(table, dict(columns={}, fks={}, uqs=set(), ixs={}, pk=set()))
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and any(isinstance(x, ast.Name) and x.id == "__table_args__" for x in stmt.targets):
                for call in ast.walk(stmt.value):
                    if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "Index":
                        name = call.args[0].value; cols = [a.value for a in call.args[1:]]
                        t["ixs"][name] = cols
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)): continue
            col = stmt.target.id; ann = src(stmt.annotation); val = stmt.value
            if col == "__tablename__" or val is None: continue
            if isinstance(val, ast.Call) and getattr(val.func, "id", "") in ("uuid_pk",):
                t["columns"][col] = ("Uuid", False); t["pk"].add(col); continue
            if isinstance(val, ast.Call) and getattr(val.func, "id", "") in ("created_at_col", "updated_at_col"):
                t["columns"][col] = ("DateTime", False); continue
            if not (isinstance(val, ast.Call) and getattr(val.func, "id", "") == "mapped_column"): continue
            kws = {k.arg: k.value for k in val.keywords}
            type_text = ""; fk = None
            for a in val.args:
                if isinstance(a, ast.Call) and getattr(a.func, "id", "") == "ForeignKey":
                    fk = a
                else: type_text = src(a)
            if not type_text:
                type_text = "Text" if "str" in ann else ann
            nullable = ("| None" in ann)
            if "nullable" in kws: nullable = bool(kws["nullable"].value)
            is_pk = "primary_key" in kws and kws["primary_key"].value is True
            if is_pk: nullable = False; t["pk"].add(col)
            t["columns"][col] = (coarse_type(type_text), nullable)
            if "unique" in kws and kws["unique"].value is True: t["uqs"].add(f"uq_{table}_{col}")
            if "index" in kws and kws["index"].value is True: t["ixs"][f"ix_{table}_{col}"] = [col]
            if fk is not None:
                ref = fk.args[0].value; ondelete = next((k.value.value for k in fk.keywords if k.arg == "ondelete"), None)
                t["fks"][f"fk_{table}_{col}_{ref.split('.')[0]}"] = (col, ref, ondelete)

# ---------- migrations ----------
MIG = {}
for path in sorted((BACKEND / "alembic/versions").glob("*.py")):
    tree = ast.parse(path.read_text())
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call): continue
        fn = src(call.func)
        if fn == "op.create_table":
            table = call.args[0].value
            t = MIG.setdefault(table, dict(columns={}, fks={}, uqs=set(), ixs={}, pk=set()))
            for item in call.args[1:]:
                if not isinstance(item, ast.Call): continue
                name = src(item.func)
                if name == "sa.Column":
                    col = item.args[0].value
                    type_text = src(item.args[1]) if len(item.args) > 1 else ""
                    kws = {k.arg: k.value for k in item.keywords}
                    nullable = bool(kws["nullable"].value) if "nullable" in kws else True
                    t["columns"][col] = (coarse_type(type_text), nullable)
                elif name == "sa.PrimaryKeyConstraint":
                    t["pk"] = {a.value for a in item.args}; t["pkname"] = next(k.value.value for k in item.keywords if k.arg == "name")
                elif name == "sa.ForeignKeyConstraint":
                    cols = [e.value for e in item.args[0].elts]; refs = [e.value for e in item.args[1].elts]
                    kw = {k.arg: k.value.value for k in item.keywords}
                    t["fks"][kw["name"]] = (cols[0], refs[0], kw.get("ondelete"))
                elif name == "sa.UniqueConstraint":
                    t["uqs"].add(next(k.value.value for k in item.keywords if k.arg == "name"))
        elif fn == "op.create_index":
            name, table = call.args[0].value, call.args[1].value
            MIG.setdefault(table, dict(columns={}, fks={}, uqs=set(), ixs={}, pk=set()))["ixs"][name] = [e.value for e in call.args[2].elts]

# ---------- compare ----------
if set(MODELS) != set(MIG): problems.append(f"table sets differ: models-only={sorted(set(MODELS)-set(MIG))} migrations-only={sorted(set(MIG)-set(MODELS))}")
for table in sorted(set(MODELS) & set(MIG)):
    m, g = MODELS[table], MIG[table]
    if set(m["columns"]) != set(g["columns"]): problems.append(f"{table}: column sets differ models-only={sorted(set(m['columns'])-set(g['columns']))} migration-only={sorted(set(g['columns'])-set(m['columns']))}")
    for col in sorted(set(m["columns"]) & set(g["columns"])):
        if m["columns"][col] != g["columns"][col]: problems.append(f"{table}.{col}: model {m['columns'][col]} != migration {g['columns'][col]}")
    if m["pk"] != g["pk"]: problems.append(f"{table}: pk columns differ {m['pk']} vs {g['pk']}")
    if g.get("pkname") != f"pk_{table}": problems.append(f"{table}: pk name {g.get('pkname')} != pk_{table}")
    if m["fks"] != g["fks"]: problems.append(f"{table}: fks differ\n   model={m['fks']}\n   migr ={g['fks']}")
    if m["uqs"] != g["uqs"]: problems.append(f"{table}: unique constraints differ {m['uqs']} vs {g['uqs']}")
    if m["ixs"] != g["ixs"]: problems.append(f"{table}: indexes differ\n   model={m['ixs']}\n   migr ={g['ixs']}")
print(f"tables: models={len(MODELS)} migrations={len(MIG)}")
print(f"columns compared: {sum(len(v['columns']) for v in MODELS.values())}; fks: {sum(len(v['fks']) for v in MODELS.values())}; indexes: {sum(len(v['ixs']) for v in MODELS.values())}")
for p in problems: print("PROBLEM:", p)
print("RESULT:", "OK" if not problems else f"{len(problems)} problem(s)")
sys.exit(1 if problems else 0)
