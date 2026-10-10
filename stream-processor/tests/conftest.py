"""Unit tests for stream-processor logic. No Kafka, ClickHouse or real Redis:
Redis is replaced by fakeredis, which implements the commands we use."""
from __future__ import annotations

import os
import sys

# The service imports its modules as top-level names (`from config import
# settings`), exactly as `python main.py` does inside the container.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
