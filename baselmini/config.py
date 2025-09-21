import json, os
from typing import Any, Dict # Typing helpers

# Load configuration file (YAML preferred, JSON fallback)
def load_config(path: str) -> Dict[str, Any]:
    # Try YAML if available, else accept JSON
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)  # Supports flexible config in YAML (risk weights, LCR params, scenarios)
    except Exception:
        # Fallback: try JSON if YAML not available or fails to parse
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


# Helper: read version dynamically from pyproject.toml (works in editable installs too)
def get_version() -> str:
    """
    Best-effort version resolver:
      1) importlib.metadata for installed/editable packages
      2) read pyproject.toml ([project].version) as fallback
      3) return '0.0.0' if all else fails
    """
    # Try importlib.metadata (works in editable installs too)
    try:
        try:
            from importlib.metadata import version  # Py3.8+
        except Exception:
            from importlib_metadata import version  # backport if needed
        return version("baselmini")
    except Exception:
        pass

    # Fallback: read pyproject.toml near repo root
    import os
    here = os.path.abspath(os.path.dirname(__file__))
    repo_root = os.path.abspath(os.path.join(here, os.pardir))  # <-- one level up
    pyproj = os.path.join(repo_root, "pyproject.toml")
    try:
        try:
            import tomllib  # Py3.11+
            with open(pyproj, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            import tomli  # pip install tomli on Py3.10
            with open(pyproj, "rb") as f:
                data = tomli.load(f)
        return str(((data or {}).get("project") or {}).get("version") or "0.0.0")
    except Exception:
        return "0.0.0"
