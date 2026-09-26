#!/bin/sh
# Deterministic turn-fixture agent launcher for the critical-turns browser
# audit. The ACP transport spawns `<command> acp …` (the transport's base
# args); this wrapper ignores that injected marker and execs the fixture
# agent directly. E2E-only — never wired into a production profile.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$DIR/../.venv/bin/python" "$DIR/turn_e2e_fixture_agent.py" "$@"
