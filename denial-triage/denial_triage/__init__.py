"""Denial triage engine for hospital radiology claims.

Rules live in rules.yaml (rules as data); the engine only interprets them.
"""

from .loader import (
    Line, SameDayLine, Extract, PHIError,
    load_rules, load_workbook_lines, load_csv_lines, load_extracts,
)
from .engine import evaluate_line, evaluate_all, Finding, Ground
from .clustering import cluster_findings, Cluster
from .report import write_all

__all__ = [
    "Line", "SameDayLine", "Extract", "PHIError",
    "load_rules", "load_workbook_lines", "load_csv_lines", "load_extracts",
    "evaluate_line", "evaluate_all", "Finding", "Ground",
    "cluster_findings", "Cluster", "write_all",
]
