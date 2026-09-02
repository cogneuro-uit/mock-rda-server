#!/usr/bin/env python3
"""Vendor wheels, Python tarballs and uv binaries for offline bootstrap.

Single-source Python implementation used by the thin cmd.exe shims on Windows
and available on Linux/macOS as well.  Uses only the Python 3.8 stdlib.

Subcommands:
  refresh   -- refresh vendor/wheels, vendor/python and the manifests
  verify    -- verify vendor/ against vendor/MANIFEST.txt
  ensure-uv -- install the pinned uv binary into .tools/
"""

import argparse
import fnmatch
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

UV_VERSION = "0.12.9"
PYTHON_TAG = "20260901"


def root_dir() -> Path:
    path = os.environ.get("PYVENDOR_ROOT")
    if path:
        return Path(path).resolve()
    return Path(__file__).resolve().parent.parent


def vendor_dir() -> Path:
    return root_dir() / "vendor"


def tools_dir() -> Path:
    return root_dir() / ".tools"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _platform_tuple() -> tuple:
    """Return (vendor_subdir, uv_release_plat).

    Raises RuntimeError on unsupported platforms.
    """
    if _is_windows():
        machine = platform.machine().lower()
        if machine not in ("x86_64", "amd64", "i386", "i686"):
            raise RuntimeError(f"unsupported Windows architecture: {machine}")
        return ("windows-x86_64", "x86_64-pc-windows-msvc")

    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return ("linux-x86_64", "x86_64-unknown-linux-gnu")
        if machine in ("aarch64", "arm64"):
            return ("linux-aarch64", "aarch64-unknown-linux-gnu")
    elif system == "darwin":
        if machine in ("arm64", "aarch64"):
            return ("darwin-arm64", "aarch64-apple-darwin")
        if machine == "x86_64":
            return ("darwin-x86_64", "x86_64-apple-darwin")

    raise RuntimeError(f"unsupported platform: {system} {machine}")


def _uv_executable_name() -> str:
    return "uv.exe" if _is_windows() else "uv"


def _uvx_executable_name() -> str:
    return "uvx.exe" if _is_windows() else "uvx"


def _run(cmd: list, cwd: Path = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"==> running {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


def _run_quiet(cmd: list) -> bool:
    """Run a command and return True only if it exits 0."""
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except Exception:
        return False


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_valid_tarball(path: Path) -> bool:
    try:
        with tarfile.open(path, "r:gz") as tf:
            tf.next()
        return True
    except Exception:
        return False


def _fetch_url(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _download_with_fallback(
    dest: Path,
    sources: list,
    description: str,
    timeout: int = 120,
) -> None:
    tried = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    for url in sources:
        try:
            data = _fetch_url(url, timeout=timeout)
            dest.write_bytes(data)
            return
        except Exception as exc:
            tried.append(f"{url}: {exc}")
            continue
    print(f"ERROR: failed to download {description}", file=sys.stderr)
    for entry in tried:
        print(f"  tried {entry}", file=sys.stderr)
    raise RuntimeError(f"could not download {description}")


def _make_executable(path: Path) -> None:
    if not _is_windows():
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)


def _resolve_uv_wheel_url() -> str:
    """Find the uv wheel URL for this platform on PyPI.

    Uses the simple index HTML and regex, as required for stdlib-only use.
    """
    vendor_subdir, uv_plat = _platform_tuple()

    candidates = {
        "linux-x86_64": [
            f"uv-{UV_VERSION}-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
            f"uv-{UV_VERSION}-py3-none-manylinux_2_28_x86_64.whl",
            f"uv-{UV_VERSION}-py3-none-musllinux_1_2_x86_64.whl",
        ],
        "linux-aarch64": [
            f"uv-{UV_VERSION}-py3-none-manylinux_2_28_aarch64.whl",
            f"uv-{UV_VERSION}-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
            f"uv-{UV_VERSION}-py3-none-musllinux_1_2_aarch64.whl",
        ],
        "darwin-arm64": [
            f"uv-{UV_VERSION}-py3-none-macosx_11_0_arm64.whl",
            f"uv-{UV_VERSION}-py3-none-macosx_10_12_x86_64.macosx_11_0_arm64.macosx_10_12_universal2.whl",
        ],
        "darwin-x86_64": [
            f"uv-{UV_VERSION}-py3-none-macosx_10_12_x86_64.whl",
            f"uv-{UV_VERSION}-py3-none-macosx_10_12_x86_64.macosx_11_0_arm64.macosx_10_12_universal2.whl",
        ],
        "windows-x86_64": [
            f"uv-{UV_VERSION}-py3-none-win_amd64.whl",
        ],
    }[vendor_subdir]

    html = _fetch_url("https://pypi.org/simple/uv/", timeout=30).decode("utf-8")
    matches = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', html, re.IGNORECASE)
    urls = {}
    for href, text in matches:
        fname = text.strip()
        urls[fname] = href

    for fname in candidates:
        if fname in urls:
            href = urls[fname]
            if href.startswith("/"):
                href = f"https://pypi.org{href}"
            return href

    raise RuntimeError(f"no uv {UV_VERSION} wheel found on PyPI for {vendor_subdir}")


def _extract_uv_from_wheel(whl_path: Path) -> None:
    tools_dir().mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(whl_path) as zf:
        for name in zf.namelist():
            if fnmatch.fnmatch(name, "*.data/scripts/uv*"):
                data = zf.read(name)
                dest = tools_dir() / Path(name).name
                dest.write_bytes(data)
                _make_executable(dest)


def _copy_vendored_uv(vendored_dir: Path) -> bool:
    uv_name = _uv_executable_name()
    if not (vendored_dir / uv_name).exists():
        return False
    tools_dir().mkdir(parents=True, exist_ok=True)
    copied = []
    for src in vendored_dir.iterdir():
        if src.name.startswith("uv"):
            dest = tools_dir() / src.name
            shutil.copy2(src, dest)
            _make_executable(dest)
            copied.append(src.name)
    return (tools_dir() / uv_name).exists()


def ensure_uv() -> None:
    """Install .tools/uv(+.exe) and uvx(+.exe), preferring the vendored copy."""
    uv_name = _uv_executable_name()
    uvx_name = _uvx_executable_name()
    uv_dest = tools_dir() / uv_name
    uvx_dest = tools_dir() / uvx_name

    if uv_dest.exists() and uvx_dest.exists():
        return

    vendor_subdir, uv_plat = _platform_tuple()
    vendored_dir = vendor_dir() / "uv-bin" / vendor_subdir

    if vendored_dir.exists() and _copy_vendored_uv(vendored_dir):
        print(f"==> uv {UV_VERSION} installed from vendor/uv-bin/{vendor_subdir}")
        return

    # PyPI wheel fallback.
    print("  vendored uv not available; trying PyPI wheel ...")
    try:
        whl_url = _resolve_uv_wheel_url()
        with tempfile.TemporaryDirectory() as td:
            whl_path = Path(td) / "uv.whl"
            _download_with_fallback(
                whl_path,
                [whl_url],
                f"uv {UV_VERSION} wheel from PyPI",
            )
            _extract_uv_from_wheel(whl_path)
        print(f"==> uv {UV_VERSION} installed from PyPI wheel")
        return
    except Exception as exc:
        print(f"  PyPI wheel failed: {exc}", file=sys.stderr)

    # GitHub release fallback.
    print("  trying GitHub release ...")
    try:
        with tempfile.TemporaryDirectory() as td:
            if _is_windows():
                url = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{uv_plat}.zip"
                archive = Path(td) / "uv.zip"
                _download_with_fallback(
                    archive,
                    [url],
                    f"uv {UV_VERSION} zip from GitHub",
                )
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(td)
                subdir = td / f"uv-{uv_plat}"
            else:
                url = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{uv_plat}.tar.gz"
                archive = Path(td) / "uv.tar.gz"
                _download_with_fallback(
                    archive,
                    [url],
                    f"uv {UV_VERSION} tarball from GitHub",
                )
                with tarfile.open(archive, "r:gz") as tf:
                    tf.extractall(td)
                subdir = td / f"uv-{uv_plat}"

            for src in subdir.iterdir():
                if src.name.startswith("uv"):
                    dest = tools_dir() / src.name
                    shutil.copy2(src, dest)
                    _make_executable(dest)
        print(f"==> uv {UV_VERSION} installed from GitHub release")
        return
    except Exception as exc:
        print(f"  GitHub release failed: {exc}", file=sys.stderr)

    raise RuntimeError(
        "could not install uv; tried vendor/uv-bin, PyPI wheel, and GitHub release"
    )


def _pip_command() -> list:
    """Return a working pip invocation, preferring sys.executable -m pip."""
    if _run_quiet([sys.executable, "-m", "pip", "--version"]):
        return [sys.executable, "-m", "pip"]

    uvx = tools_dir() / _uvx_executable_name()
    if uvx.exists() and _run_quiet([str(uvx), "--from", "pip", "pip", "--version"]):
        return [str(uvx), "--from", "pip", "pip"]

    raise RuntimeError(
        "no usable pip found (tried 'python -m pip' and '.tools/uvx --from pip pip')"
    )


def _pip_download(
    pip: list,
    dest: Path,
    reqs_file: Path = None,
    packages: list = None,
    target_platform: str = None,
) -> None:
    cmd = pip + ["download"]
    if reqs_file is not None:
        cmd += ["-r", str(reqs_file)]
    if packages is not None:
        cmd += packages
    if target_platform is not None:
        cmd += [
            "--platform", target_platform,
            "--python-version", "3.12",
            "--only-binary=:all:",
        ]
    cmd += ["-d", str(dest)]
    _run(cmd, cwd=root_dir())


def _ensure_python_tarball(uv_plat: str) -> None:
    filename = (
        f"cpython-3.12.14+{PYTHON_TAG}-{uv_plat}-install_only_stripped.tar.gz"
    )
    dest = vendor_dir() / "python" / PYTHON_TAG / filename

    if dest.exists():
        if _is_valid_tarball(dest):
            print(f"  {filename} already present")
            return
        print(f"  {filename} is corrupt, re-downloading")
        dest.unlink()

    escaped = filename.replace("+", "%2B")
    sources = [
        f"https://releases.astral.sh/github/python-build-standalone/releases/download/{PYTHON_TAG}/{escaped}",
        f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_TAG}/{escaped}",
    ]
    print(f"==> downloading {filename} ...")
    _download_with_fallback(dest, sources, filename)
    if not _is_valid_tarball(dest):
        dest.unlink()
        raise RuntimeError(f"downloaded {filename} is not a valid tar.gz")
    print(f"    {filename} downloaded and verified")


def _flatten_reqs(reqs_path: Path) -> str:
    """Mirror vendor.sh's awk/sed flattening exactly."""
    out = []
    for raw in reqs_path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped == ".":
            continue
        cleaned = re.sub(r" *# .*$", "", raw).rstrip()
        if cleaned:
            out.append(cleaned)
    return "\n".join(out) + "\n"


def _generate_manifest() -> None:
    vendor = vendor_dir()
    entries = []
    for dirpath, _dirnames, filenames in os.walk(vendor):
        for fn in filenames:
            if fn in ("MANIFEST.txt", "MANIFEST.sha256"):
                continue
            path = Path(dirpath) / fn
            rel = path.relative_to(vendor).as_posix()
            h = _sha256_file(path)
            entries.append(f"{h}  {rel}")
    entries.sort()

    uv_version = subprocess.check_output(
        [str(tools_dir() / _uv_executable_name()), "--version"],
        text=True,
    ).strip()
    pip_version = subprocess.check_output(
        _pip_command() + ["--version"],
        text=True,
    ).strip()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017

    lines = [
        f"# Generated {generated}",
        f"# uv {uv_version}",
        f"# pip {pip_version}",
    ] + entries
    body = "\n".join(lines) + "\n"

    manifest_path = vendor / "MANIFEST.txt"
    manifest_path.write_text(body, encoding="utf-8")
    hash_path = vendor / "MANIFEST.sha256"
    hash_path.write_text(hashlib.sha256(body.encode("utf-8")).hexdigest() + "\n", encoding="utf-8")


def cmd_refresh() -> None:
    root = root_dir()
    vendor = vendor_dir()
    wheels_dir = vendor / "wheels"
    python_dir = vendor / "python" / PYTHON_TAG
    wheels_dir.mkdir(parents=True, exist_ok=True)
    python_dir.mkdir(parents=True, exist_ok=True)

    ensure_uv()

    print("==> exporting locked requirements to vendor/reqs.txt ...")
    reqs = vendor / "reqs.txt"
    _run(
        [
            str(tools_dir() / _uv_executable_name()),
            "export",
            "--frozen",
            "--no-hashes",
            "--extra", "test",
            "--group", "dev",
            "--no-editable",
            "-o", str(reqs),
        ],
        cwd=root,
    )

    print("==> flattening vendor/reqs.txt to vendor/reqs-flat.txt ...")
    flat = _flatten_reqs(reqs)
    flat_path = vendor / "reqs-flat.txt"
    flat_path.write_text(flat, encoding="utf-8")

    # Wipe only wheels; leave python tarballs and uv-bin untouched.
    for f in wheels_dir.glob("*.whl"):
        f.unlink()

    pip = _pip_command()
    vendor_subdir, _uv_plat = _platform_tuple()

    if vendor_subdir == "windows-x86_64":
        print("==> downloading Windows win_amd64 wheels ...")
        _pip_download(pip, wheels_dir, reqs_file=flat_path, target_platform="win_amd64")
    else:
        print("==> downloading current-platform wheels ...")
        _pip_download(pip, wheels_dir, reqs_file=flat_path)
        print("==> downloading Windows win_amd64 wheels ...")
        _pip_download(pip, wheels_dir, reqs_file=flat_path, target_platform="win_amd64")

    print("==> downloading hatchling + transitive build deps ...")
    _pip_download(pip, wheels_dir, packages=["hatchling", "editables"])

    if vendor_subdir == "windows-x86_64":
        _ensure_python_tarball("x86_64-pc-windows-msvc")
    else:
        _ensure_python_tarball("x86_64-unknown-linux-gnu")
        _ensure_python_tarball("x86_64-pc-windows-msvc")

    print("==> generating vendor/MANIFEST.txt ...")
    _generate_manifest()

    wheel_count = len(list(wheels_dir.glob("*.whl")))
    py_count = len(list((vendor / "python").rglob("*.tar.gz")))
    total_bytes = sum(
        p.stat().st_size
        for p in vendor.rglob("*")
        if p.is_file() and p.name not in ("MANIFEST.txt", "MANIFEST.sha256")
    )
    total_size = f"{total_bytes / (1024 * 1024):.1f} MB"
    if total_bytes >= 1024 ** 3:
        total_size = f"{total_bytes / (1024 ** 3):.1f} GB"

    print("")
    print("Vendor summary:")
    print(f"  wheels      : {wheel_count}")
    print(f"  python tars : {py_count}")
    print(f"  total size  : {total_size}")
    print("")
    print("Commit vendor/ to git so clones bootstrap fully offline.")


def cmd_verify() -> int:
    vendor = vendor_dir()
    manifest_path = vendor / "MANIFEST.txt"
    hash_path = vendor / "MANIFEST.sha256"

    if not manifest_path.exists():
        print("ERROR: vendor/MANIFEST.txt not found.", file=sys.stderr)
        return 1
    if not hash_path.exists():
        print("ERROR: vendor/MANIFEST.sha256 not found.", file=sys.stderr)
        return 1

    print("==> verifying vendor/MANIFEST.txt ...")
    expected = hash_path.read_text(encoding="utf-8").strip()
    actual = _sha256_file(manifest_path)
    if expected != actual:
        print(
            f"ERROR: MANIFEST.txt hash mismatch (expected {expected}, got {actual})",
            file=sys.stderr,
        )
        return 1

    print("==> verifying vendored files against vendor/MANIFEST.txt ...")
    missing = 0
    mismatches = 0
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            continue
        expected_hash, rel = parts
        path = vendor / rel
        if not path.exists():
            print(f"  MISSING: {rel}", file=sys.stderr)
            missing += 1
            continue
        actual_hash = _sha256_file(path)
        if actual_hash != expected_hash:
            print(
                f"  MISMATCH: {rel} (expected {expected_hash}, got {actual_hash})",
                file=sys.stderr,
            )
            mismatches += 1

    if missing > 0 or mismatches > 0:
        print(
            f"ERROR: {missing} missing, {mismatches} mismatched.",
            file=sys.stderr,
        )
        return 1

    print("==> all vendored files passed the integrity check")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vendor wheels, Python tarballs and uv binaries for offline bootstrap.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("refresh", help="refresh vendor/wheels and vendor/python")
    sub.add_parser("verify", help="verify vendor/ against MANIFEST.txt")
    sub.add_parser("ensure-uv", help="install the pinned uv binary into .tools/")
    args = parser.parse_args()

    try:
        if args.command == "refresh":
            cmd_refresh()
            return 0
        if args.command == "verify":
            return cmd_verify()
        if args.command == "ensure-uv":
            ensure_uv()
            return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
