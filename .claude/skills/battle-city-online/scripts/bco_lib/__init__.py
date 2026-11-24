"""Shared library for the Battle City Online skill (stdlib only)."""
import os
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REFERENCES_DIR = os.path.join(SKILL_DIR, "references")
SCHEMA_PATH = os.path.join(REFERENCES_DIR, "state-schema.json")

# Windows consoles default to cp1252/cp437: force UTF-8 so Vietnamese text and emoji print.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
