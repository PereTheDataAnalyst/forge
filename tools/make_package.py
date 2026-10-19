"""Build a Forge package from the diff between a base tree and a working tree.

For the engineer's sandbox, not for the owner. Usage:

    python3 tools/make_package.py BASE_DIR WORK_DIR PACKAGE_NAME [--out DIR]

BASE_DIR must be the OWNER'S repository state (the newest snapshot, or the previous base with
the previous package applied: prove they are equal first). WORK_DIR is the working tree. The tool
writes PACKAGE_NAME.zip with the changed and new files plus .package/MANIFEST.txt, in the format
scripts/install-package.ps1 verifies, and enforces the repository's line-ending rules (LF
everywhere, CRLF for .ps1, .bat and .cmd). Then it simulates the installer on a clean copy of
BASE_DIR and proves that the package reproduces WORK_DIR byte for byte. It refuses to build when a
file was deleted, because a package cannot delete files. It prints the dry run the owner should
see (counts and file names): copy those numbers into the commands file, never estimate them.
Generated folders (__pycache__, dist, shots, node_modules, .venv) are never packaged.
"""
import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

SKIP_PARTS = {"__pycache__", ".git", "node_modules", ".venv", ".forge-backups", "dist", "shots"}
CRLF_SUFFIXES = {".ps1", ".bat", ".cmd"}


def tree(root: Path) -> dict[str, Path]:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not (set(path.relative_to(root).parts) & SKIP_PARTS):
            files[path.relative_to(root).as_posix()] = path
    return files


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_endings(rel: str, data: bytes) -> None:
    suffix = Path(rel).suffix.lower()
    has_crlf = b"\r\n" in data
    bare_lf = data.replace(b"\r\n", b"").count(b"\n") > 0
    if suffix in CRLF_SUFFIXES:
        assert has_crlf and not bare_lf, f"{rel}: must be pure CRLF"
    elif suffix not in {".png", ".ico", ".woff", ".woff2", ".zip"}:
        assert not has_crlf, f"{rel}: must be LF only"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base, work, name = Path(args[0]), Path(args[1]), args[2]
    out = Path("/mnt/user-data/outputs")
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
    base_files, work_files = tree(base), tree(work)

    removed = sorted(set(base_files) - set(work_files))
    assert not removed, f"files deleted in working tree (packages cannot delete): {removed}"
    changes = {rel: p for rel, p in work_files.items() if rel not in base_files or sha(p) != sha(base_files[rel])}
    assert changes, "nothing changed"

    for rel, path in changes.items():
        check_endings(rel, path.read_bytes())

    staging = Path(tempfile.mkdtemp())
    manifest = ["# SHA-256 manifest for scripts/install-package.ps1", f"# package: {name}"]
    for rel in sorted(changes):
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(changes[rel], dest)
        manifest.append(f"{sha(dest)}  {rel}")
    (staging / ".package").mkdir()
    (staging / ".package" / "MANIFEST.txt").write_bytes(("\n".join(manifest) + "\n").encode("utf-8"))

    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(staging).as_posix(), date_time=(2026, 10, 15, 12, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())

    # Simulate extract + installer on a clean copy of the base, then compare with the working tree.
    sim = Path(tempfile.mkdtemp()) / "repo"
    shutil.copytree(base, sim, ignore=shutil.ignore_patterns(*SKIP_PARTS))
    extract = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
        archive.extractall(extract)
    listed = [l for l in (extract / ".package" / "MANIFEST.txt").read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    for line in listed:
        digest, rel = line.split("  ", 1)
        assert sha(extract / rel) == digest, f"manifest mismatch {rel}"
    new, overwrite = [], []
    for line in listed:
        rel = line.split("  ", 1)[1]
        (overwrite if (sim / rel).exists() else new).append(rel)
        (sim / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(extract / rel, sim / rel)
    sim_files = tree(sim)
    assert set(sim_files) == set(work_files), "simulated install has different file set"
    for rel in work_files:
        assert sha(sim_files[rel]) == sha(work_files[rel]), f"simulated install differs: {rel}"

    print(f"package: {zip_path}  ({zip_path.stat().st_size} bytes)")
    print(f"zip sha256: {hashlib.sha256(zip_path.read_bytes()).hexdigest().upper()}")
    print(f"files in manifest: {len(listed)}  (files in zip incl. manifest: {len(listed) + 1})")
    print(f"dry run will report: new: {len(new)}   unchanged: 0   would overwrite: {len(overwrite)}")
    for rel in overwrite:
        print("  would overwrite:", rel.replace("/", "\\"))
    for rel in new:
        print("  new:", rel.replace("/", "\\"))
    print("simulated install reproduces the working tree byte for byte: OK")


main()
