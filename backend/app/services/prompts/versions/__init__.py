"""Versioned prompts for FreightWatch LLM services.

Each prompt file contains:
- VERSION:         Semantic version string (vMAJOR.MINOR.PATCH)
- SYSTEM_PROMPT:   System instruction for the LLM
- PROMPT_TEMPLATE: The actual prompt template (use {variable} placeholders)
- CHANGELOG:       What changed from the previous version

Version naming convention:
- MAJOR: Breaking changes (output JSON schema changed, required fields added/removed)
- MINOR: New features, significant prompt improvements, new extraction fields
- PATCH: Bug fixes, clarifications, small wording adjustments
"""

from __future__ import annotations

import glob
import importlib
import os
import re
from typing import Any


def get_prompt_version(prompt_name: str, version: str) -> dict[str, Any]:
    """Load a specific prompt version.

    Args:
        prompt_name: Name of the prompt (e.g. 'freight_invoice_extractor')
        version:     Version string (e.g. 'v1.0.0')

    Returns:
        Dict with VERSION, SYSTEM_PROMPT, PROMPT_TEMPLATE, CHANGELOG.

    Raises:
        ImportError: If the requested version file does not exist.
    """
    module_name = (
        f"app.services.prompts.versions."
        f"{prompt_name}_{version.replace('.', '_')}"
    )
    try:
        module = importlib.import_module(module_name)
        return {
            "VERSION": module.VERSION,
            "SYSTEM_PROMPT": module.SYSTEM_PROMPT,
            "PROMPT_TEMPLATE": module.PROMPT_TEMPLATE,
            "CHANGELOG": getattr(module, "CHANGELOG", ""),
        }
    except ImportError as exc:
        raise ImportError(
            f"Prompt version '{version}' not found for '{prompt_name}'. "
            f"Expected file: {module_name.replace('.', '/')}.py"
        ) from exc


def list_versions(prompt_name: str) -> list[str]:
    """Return all available versions for a prompt, newest first."""
    versions_dir = os.path.dirname(__file__)
    pattern = os.path.join(versions_dir, f"{prompt_name}_v*.py")
    files = glob.glob(pattern)

    version_re = re.compile(
        rf"^{re.escape(prompt_name)}_v(\d+)_(\d+)_(\d+)\.py$"
    )
    found: list[str] = []
    for path in files:
        m = version_re.match(os.path.basename(path))
        if m:
            major, minor, patch = m.groups()
            found.append(f"v{major}.{minor}.{patch}")

    def _key(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.lstrip("v").split("."))

    return sorted(found, key=_key, reverse=True)
