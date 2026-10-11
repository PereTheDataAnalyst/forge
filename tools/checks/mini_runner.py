"""Minimal pytest-style runner for fixture-free test functions (sandbox only)."""
import importlib, sys, time, traceback
sys.path.insert(0, sys.argv[1])
failed = passed = 0
for modname in sys.argv[2:]:
    mod = importlib.import_module(modname)
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        try:
            getattr(mod, name)(); passed += 1; print(f"PASS {modname}.{name}")
        except Exception:
            failed += 1; print(f"FAIL {modname}.{name}"); traceback.print_exc()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
