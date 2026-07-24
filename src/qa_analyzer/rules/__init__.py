"""Four core reportability tests from VICTIG SOP §5 (Pending Review).

Test 1: Reportable offense level (felony/misdemeanor or state equivalent)
Test 2: Within scope of reporting (FCRA + state + client restrictions)
Test 3: Reportable disposition (conviction or active pending/deferred)
Test 4: Sufficient PII to match record to candidate (NameGrade-driven)

Each test returns a RuleResult with a full reasoning trail.
"""

from qa_analyzer.rules.test1_offense import evaluate as test1_offense
from qa_analyzer.rules.test2_scope import evaluate as test2_scope, controlling_date_for
from qa_analyzer.rules.test3_disposition import evaluate as test3_disposition
from qa_analyzer.rules.test4_pii import evaluate as test4_pii

__all__ = [
    "test1_offense",
    "test2_scope",
    "test3_disposition",
    "test4_pii",
    "controlling_date_for",
]
