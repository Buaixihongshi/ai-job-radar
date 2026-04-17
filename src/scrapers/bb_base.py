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


def ensure_adapters_linked() -> None:
    """Ensure project adapters are symlinked to ~/.bb-browser/sites/."""
    bb_sites_dir = Path.home() / ".bb-browser" / "sites"
    bb_sites_dir.mkdir(parents=True, exist_ok=True)

    for adapter_dir in ADAPTERS_DIR.iterdir():
        if not adapter_dir.is_dir():
            continue
        target = bb_sites_dir / adapter_dir.name
        if target.exists() or target.is_symlink():
            continue
        try:
            target.symlink_to(adapter_dir.resolve())
            logger.info("Linked adapter: %s -> %s", target, adapter_dir)
        except OSError:
            logger.warning("Failed to symlink adapter %s", adapter_dir.name)
