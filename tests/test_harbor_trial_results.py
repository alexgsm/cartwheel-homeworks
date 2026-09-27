"""Harbor 0.23.0 leaves trial_results out of the job result.json.

The summary and trial-count analysis must then read each trial's own
result.json, in the order the trials started.
"""

from __future__ import annotations

import json
from pathlib import Path

from harbor_adapter.summary import load_trial_results


def _trial(name: str, started_at: str, reward: float) -> dict:
    return {
        "task_name": "cartwheel/evals__e-001",
        "trial_name": name,
        "started_at": started_at,
        "verifier_result": {"rewards": {"reward": reward}},
    }


def test_reads_per_trial_results_in_start_order(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    # Directory names sort differently from start times on purpose.
    for name, started, reward in [
        ("e-001__zzz", "2026-09-26T20:24:11Z", 1.0),
        ("e-001__aaa", "2026-09-26T20:24:13Z", 0.0),
        ("e-001__mmm", "2026-09-26T20:24:12Z", 1.0),
    ]:
        (job / name).mkdir()
        (job / name / "result.json").write_text(json.dumps(_trial(name, started, reward)))
    job_result = {"id": "x", "n_total_trials": 3, "stats": {}}
    (job / "result.json").write_text(json.dumps(job_result))

    trials = load_trial_results(job, job_result)

    assert [t["trial_name"] for t in trials] == ["e-001__zzz", "e-001__mmm", "e-001__aaa"]


def test_embedded_trial_results_still_win(tmp_path: Path) -> None:
    embedded = [_trial("e-001__b", "2", 1.0), _trial("e-001__a", "1", 0.0)]

    trials = load_trial_results(tmp_path, {"trial_results": embedded})

    assert [t["trial_name"] for t in trials] == ["e-001__b", "e-001__a"]
