"""Base wrapper for bb-browser CLI integration.

bb-browser connects to your real Chrome browser via CDP, allowing
Python code to navigate pages and extract data with your login state.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

BB_CMD = "bb-browser"
ADAPTERS_DIR = Path(__file__).parent.parent.parent / "adapters"

# bb-browser 0.8.x 不会自动扫描 ~/.bb-browser/sites/ 下的私有 adapter，
# 因此我们用 bb_eval 直接执行 adapter JS（IIFE 形式），不依赖 CLI 注册。


def bb_is_available() -> bool:
    """Check if bb-browser CLI is installed and can talk to Chrome."""
    if not shutil.which(BB_CMD):
        return False
    try:
        result = subprocess.run(
            [BB_CMD, "tab", "list", "--json"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        return data.get("success", False)
    except Exception:
        return False


def bb_open(url: str, timeout: int = 15) -> str:
    """Navigate Chrome to *url*. Returns the new tab ID."""
    result = subprocess.run(
        [BB_CMD, "open", url],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"bb-browser open failed: {result.stderr or result.stdout}")
    return result.stdout.strip()


def bb_eval(js: str, timeout: int = 15) -> str | dict | list | None:
    """Execute JavaScript in the active tab and return the result.

    If the result is valid JSON, it is parsed automatically.
    """
    result = subprocess.run(
        [BB_CMD, "eval", js, "--json"],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"bb-browser eval failed: {result.stderr or result.stdout}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()

    inner = envelope.get("data", {}).get("result")
    if isinstance(inner, str):
        try:
            return json.loads(inner)
        except (json.JSONDecodeError, TypeError):
            pass
    return inner


def bb_run_site(command: str, args: dict | None = None, timeout: int = 30) -> dict:
    """Run a bb-browser site adapter and return parsed JSON."""
    cmd = [BB_CMD, "site", command]
    if args:
        for key, value in args.items():
            if value is not None and value != "":
                cmd.extend([f"--{key}", str(value)])
    cmd.append("--json")

    logger.debug("bb-browser cmd: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"bb-browser timed out after {timeout}s: {command}")

    stdout = result.stdout.strip()
    if result.returncode != 0:
        raise RuntimeError(f"bb-browser exit {result.returncode}: {result.stderr or stdout}")
    if not stdout:
        raise RuntimeError(f"bb-browser returned empty output: {command}")

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"bb-browser returned non-JSON: {stdout[:200]}")


def bb_run_adapter(adapter_path: str | Path, args: dict | None = None,
                   timeout: int = 30) -> dict | list | str | int | None:
    """Execute a project adapter JS file in the active tab via ``bb_eval``.

    The adapter file must define a single top-level ``async function(args) {…}``
    expression (optionally preceded by a ``/* @meta … */`` block). We wrap it
    as an IIFE and call it with the provided *args* dict.

    The active tab must already be on the adapter's expected origin so that
    ``fetch('/api/...')`` calls go to the right server with cookies.

    Returns whatever the adapter returns (after envelope unwrapping by
    ``bb_eval``), typically a dict like ``{count, jobs: [...]}``.
    """
    path = Path(adapter_path)
    if not path.exists():
        raise FileNotFoundError(f"adapter not found: {path}")

    js_body = path.read_text(encoding="utf-8")
    args_json = json.dumps(args or {}, ensure_ascii=False)
    iife = f"({js_body})({args_json})"
    return bb_eval(iife, timeout=timeout)


def ensure_adapters_installed() -> None:
    """Copy project adapters into ``~/.bb-browser/sites/`` for CLI discovery.

    bb-browser 0.11+ scans ``~/.bb-browser/sites/<platform>/<command>.js``
    for private adapters (shown with ``(local)`` tag in ``site list``).
    Symlinks are **not** followed by the scanner, so we copy files instead.

    This is best-effort — ``bb_run_adapter`` works regardless (it reads the
    adapter JS directly from the project tree and executes via ``bb_eval``).
    """
    bb_sites_dir = Path.home() / ".bb-browser" / "sites"
    bb_sites_dir.mkdir(parents=True, exist_ok=True)

    for adapter_dir in ADAPTERS_DIR.iterdir():
        if not adapter_dir.is_dir():
            continue
        target_dir = bb_sites_dir / adapter_dir.name
        target_dir.mkdir(parents=True, exist_ok=True)

        for js_file in adapter_dir.glob("*.js"):
            target_file = target_dir / js_file.name
            try:
                import filecmp
                if target_file.exists() and filecmp.cmp(str(js_file), str(target_file)):
                    continue
                import shutil as _shutil
                _shutil.copy2(str(js_file), str(target_file))
                logger.info("Installed adapter: %s -> %s", js_file.name, target_dir)
            except OSError:
                logger.warning("Failed to install adapter %s/%s", adapter_dir.name, js_file.name)
