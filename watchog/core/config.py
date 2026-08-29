"""Config loading.

Watch conditions live in YAML and are safe to commit. Anything secret -- ntfy
topic, API keys -- is referenced as ``${VAR}`` and resolved from the
environment, so the same file works locally and in GitHub Actions Secrets.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path("config/watch.yaml")

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            name = m.group(1)
            found = os.environ.get(name)
            if found is None:
                raise KeyError(
                    f"config references ${{{name}}} but that environment "
                    f"variable is not set"
                )
            return found

        return _ENV_REF.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load(path: str | Path = DEFAULT_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found -- copy config/watch.example.yaml to {path} "
            f"and edit it"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _expand(raw)
