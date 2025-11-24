from __future__ import annotations

from importlib import metadata
from pathlib import Path
from typing import cast

try:
    __version__: str = metadata.version("tg-feedback-bot-ru")
except metadata.PackageNotFoundError:  # pragma: no cover - local fallback
    import tomllib

    root = Path(__file__).resolve().parents[1]
    pyproject_path = root / "pyproject.toml"
    with pyproject_path.open("rb") as pyproject_file:
        pyproject: dict[str, object] = tomllib.load(pyproject_file)

    project = pyproject.get("project")
    if not isinstance(project, dict):
        msg = "'project' section missing in pyproject.toml"
        raise RuntimeError(msg) from None

    project_table = cast(dict[str, object], project)

    version_value = project_table.get("version")
    if not isinstance(version_value, str):
        msg = "'project.version' must be a string in pyproject.toml"
        raise RuntimeError(msg) from None

    __version__ = version_value

__all__ = ("__version__",)
