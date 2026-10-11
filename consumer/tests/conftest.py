from __future__ import annotations

import os
import sys

# The service imports its modules as top-level names, as `python main.py` does.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
