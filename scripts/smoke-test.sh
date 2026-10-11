#!/usr/bin/env bash
# End-to-end check of the local stack (start it first: bash scripts/dev-up.sh).
# Sends 200 fake LLM calls through the Python SDK and checks that they show
# up in the dashboard, metrics and alerts. Free: no real LLM provider calls.
# Takes about 2 minutes, mostly waiting for the 60-second metrics window.
set -euo pipefail
cd "$(dirname "$0")/.."
# MSYS_NO_PATHCONV stops Git Bash on Windows from rewriting container paths.
MSYS_NO_PATHCONV=1 docker compose --profile smoke run --rm smoke
