"""Data lifecycle manager — keeps ~/.hermes lean over long-running installs.

Compresses warm data (e.g. month-old sessions) with zstd, deletes cold
data past retention, and rotates backup files. Safe by default: every
rule supports dry-run and can be disabled independently.
"""
from hermes_cli.lifecycle.core import (
    DEFAULT_RULES,
    Rule,
    RunSummary,
    run_rules,
    status,
)

__all__ = ["DEFAULT_RULES", "Rule", "RunSummary", "run_rules", "status"]
