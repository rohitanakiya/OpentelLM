from __future__ import annotations

import pathlib
import sys

import opentelm

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_pyproject_version_matches_sdk_version():
    data = _pyproject()
    assert data["project"]["version"] == opentelm.__version__


def test_no_runtime_dependencies():
    """The SDK must not drag httpx (or anything else) into users' apps."""
    assert _pyproject()["project"]["dependencies"] == []
