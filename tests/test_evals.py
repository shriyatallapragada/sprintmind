"""Every evaluation case's deterministic checks run as a regression test (answers need `python -m evals.run --llm`)."""
from __future__ import annotations

import pytest

from evals.cases import CASES
from evals.run import run_case


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_eval_case(case):
    result = run_case(case)
    assert result["error"] is None, result["error"]
    failed = [c for c in result["checks"] if not c["ok"]]
    assert not failed, failed
