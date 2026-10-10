# All metadata lives in pyproject.toml; this shim only keeps legacy
# `python setup.py develop` / old pip editable installs working.
from setuptools import setup

setup()
