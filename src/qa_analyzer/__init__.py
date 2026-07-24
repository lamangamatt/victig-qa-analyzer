"""VICTIG QA Analyzer — SOP-driven criminal-record reportability engine."""

from qa_analyzer.models import (
    Subject,
    CriminalRecord,
    ClientProfile,
    Disposition,
    OffenseLevel,
    Decision,
    DecisionOutcome,
    RuleResult,
)
from qa_analyzer.decision import analyze_record

__all__ = [
    "Subject",
    "CriminalRecord",
    "ClientProfile",
    "Disposition",
    "OffenseLevel",
    "Decision",
    "DecisionOutcome",
    "RuleResult",
    "analyze_record",
]

__version__ = "0.1.0"
