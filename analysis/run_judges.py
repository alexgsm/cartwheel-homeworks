"""Homework 5 judge pipeline: inputs, splits, development runs, frozen test.

Run every function from the repository root.

Label conventions, which are easy to get backwards:

* Homework 4 files (``analysis/state/labels/<mode>.jsonl``) store ``1`` when the
  named failure is **present**.
* Homework 5 files (``analysis/state/hw5_labels/<mode>.jsonl``) store ``1`` for
  **Pass** and ``0`` for **Fail**, as the handout requires.
* ``analysis.helpers._load_labels`` prefers the Homework 5 file when it exists
  and flips it back to the internal failure-indicator convention. So the
  Homework 5 file must hold **every** label for the mode, not only new ones.

Usage::

    uv run python -c "from analysis.run_judges import prepare_inputs; prepare_inputs()"
    uv run python -c "from analysis.run_judges import convert_hw4_labels; convert_hw4_labels('ungrounded_claim')"
    uv run python -c "from analysis.run_judges import split_data; split_data('ungrounded_claim')"
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "analysis" / "state"
REPORT = REPO / "analysis" / "report"
TRACE_EXPORT = REPO / "traces" / "support_traces.json"
INPUTS = STATE / "hw5_trace_inputs.json"
EXCLUSIONS = STATE / "hw5_exclusions.json"

MODE = "ungrounded_claim"
JUDGE_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Part B: inputs
# ---------------------------------------------------------------------------


def _text_of(blocks: Any) -> str:
    """Flatten Langfuse's ``[{role, parts:[{type,content}]}]`` payload to text."""
    if isinstance(blocks, str):
        return blocks
    out: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        for part in block.get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(part.get("content", ""))
    return "\n".join(out).strip()


def _messages_for(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the judge-visible message list for one trace.

    Tool calls are included deliberately. A judge for a grounding criterion
    that cannot see tool results has nothing to check the reply against, and
    the failure is silent: the judge still returns confident verdicts.

    ``analysis.helpers.normalization._flatten`` renders a ``tool_call`` from its
    ``arguments`` and a ``tool_result`` from its ``content``, and drops the
    ``name`` field, so the tool name is folded into the payload to keep it
    visible to the judge.
    """
    messages: list[dict[str, Any]] = []
    user = _text_of(trace.get("input"))
    if user:
        messages.append({"role": "user", "text": user})

    tools = sorted(
        (o for o in trace.get("observations") or [] if o.get("type") == "TOOL"),
        key=lambda o: o.get("startTime") or "",
    )
    for obs in tools:
        name = str(obs.get("name") or "tool")
        messages.append(
            {"role": "tool_call", "name": name,
             "arguments": {"tool": name, "arguments": obs.get("input")}}
        )
        messages.append(
            {"role": "tool_result", "name": name,
             "content": {"tool": name, "result": obs.get("output")}}
        )

    reply = _text_of(trace.get("output"))
    if reply:
        messages.append({"role": "assistant", "text": reply})
    return messages


def prepare_inputs(export: Path = TRACE_EXPORT, out: Path = INPUTS) -> dict[str, Any]:
    """Write one judge-input record per reviewed conversation.

    One record per conversation, as the handout requires. A conversation with
    two turns becomes one record holding **both** turns in order, and the
    judge checks every agent reply in it. An earlier version kept only the
    first turn; that was wrong, because several Homework 4 labels rest on a
    claim made in the second turn (0108, 0238, 0244), so the judge would have
    been scored on text that does not contain what was labelled. The record
    keeps the first turn's trace id, which is where the conversation's label
    is stored.

    Close scenario variants are collapsed the same way: scenarios sharing an
    authorization ``pair_key`` or a ``data_quality_case_id`` are built on the
    same record, so the first scenario id in each group is kept and the rest
    are recorded as exclusions. Scenario metadata is read here only to find
    those groups; none of it is written into the judge input.

    Human labels, review notes and scenario metadata are deliberately absent.
    """
    raw = json.loads(export.read_text(encoding="utf-8"))["traces"]
    labelled = set(_hw4_labels(MODE)) | set(_candidate_labels(MODE))
    labelled_convs = {
        t.get("cartwheel_scenario_id") for t in raw if t["id"] in labelled
    }

    by_conv: dict[str, list[dict[str, Any]]] = {}
    for trace in raw:
        conv = trace.get("cartwheel_scenario_id")
        if conv in labelled_convs:
            by_conv.setdefault(conv, []).append(trace)

    records: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for group, members in sorted(_variant_groups(set(by_conv)).items()):
        for conv in sorted(members)[1:]:
            for trace in by_conv.pop(conv):
                excluded.append(
                    {"trace_id": trace["id"], "conv_id": conv,
                     "reason": f"close scenario variant ({group}); kept "
                               f"{sorted(members)[0]} as the one record for "
                               "this group"}
                )
    for conv, traces in sorted(by_conv.items()):
        traces.sort(key=lambda t: t.get("timestamp") or "")
        messages = [m for trace in traces for m in _messages_for(trace)]
        record_id = next(t["id"] for t in traces if t["id"] in labelled)
        records.append({"trace_id": record_id, "conv_id": conv, "trace": messages})

    empty = [r["conv_id"] for r in records if not r["trace"]]
    if empty:
        raise ValueError(f"records with no messages: {empty[:5]}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=1), encoding="utf-8")
    EXCLUSIONS.write_text(json.dumps(excluded, indent=1), encoding="utf-8")

    tool_calls = sum(
        1 for r in records for m in r["trace"] if m["role"] == "tool_call"
    )
    with_tools = sum(
        1 for r in records if any(m["role"] == "tool_call" for m in r["trace"])
    )
    summary = {
        "records": len(records),
        "excluded": len(excluded),
        "tool_calls": tool_calls,
        "records_with_tool_calls": with_tools,
        "path": str(out),
    }
    if tool_calls == 0:
        raise ValueError(
            "no tool calls survived into the judge input. A grounding judge "
            "cannot work without them; check the TOOL observation mapping."
        )
    return summary


def verify_inputs(out: Path = INPUTS) -> dict[str, Any]:
    """Normalize the export the way the judge will and confirm tools survive.

    This runs the real code path (``normalize_trace`` then ``_flatten``), not a
    reimplementation of it, because the failure this guards against is exactly
    a mismatch between what we write and what the helpers read.
    """
    from analysis.helpers.normalization import normalize_trace

    records = json.loads(out.read_text(encoding="utf-8"))
    normalized = [normalize_trace(r) for r in records]
    no_text = [n["trace_id"] for n in normalized if not n.get("text")]
    if no_text:
        raise ValueError(f"traces with no judge-visible text: {no_text[:5]}")

    missing_tools = [
        n["trace_id"] for n, r in zip(normalized, records)
        if any(m["role"] == "tool_call" for m in r["trace"])
        and "tool_result:" not in n["text"]
    ]
    if missing_tools:
        raise ValueError(
            f"tool results vanished during normalization for {missing_tools[:5]}"
        )
    return {
        "normalized": len(normalized),
        "total_tool_calls": sum(n["features"]["tool_call_count"] for n in normalized),
        "sample_text_head": normalized[0]["text"][:200],
    }


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------


def _hw4_labels(mode: str) -> dict[str, dict[str, Any]]:
    """Live Homework 4 label per trace (``1`` = failure present)."""
    path = STATE / "labels" / f"{mode}.jsonl"
    live: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("superseded_by"):
            continue
        live[row["trace_id"]] = row
    return live


def _candidate_labels(mode: str) -> dict[str, dict[str, Any]]:
    """Homework 5 verdicts on the enrich candidates; the last row per trace wins.

    Skips are dropped: a candidate that could not be judged has no label.
    """
    path = STATE / f"hw5_candidates_{mode}.jsonl"
    if not path.exists():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[row["trace_id"]] = row
    return {tid: row for tid, row in latest.items() if row["verdict"] in ("pass", "fail")}


def _hw4_relabels(mode: str) -> dict[str, dict[str, Any]]:
    """Homework 4 labels changed during the Homework 5 recheck, keyed by conversation.

    The definition was sharpened while labelling candidates, so the Homework 4
    labels were rechecked against it, as the handout requires. The Homework 4
    files are left untouched; each change is recorded here with its reason.
    """
    path = STATE / f"hw5_relabels_{mode}.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["conv_id"]: row for row in rows}


def _variant_groups(convs: set[str]) -> dict[str, list[str]]:
    """Groups of labelled scenarios that are close variants of each other."""
    scenarios = {
        row["id"]: row
        for row in (
            json.loads(line)
            for line in (REPO / "scenarios" / "support_scenarios.jsonl")
            .read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    groups: dict[str, list[str]] = {}
    for conv in convs:
        scenario = scenarios.get(conv, {})
        pair = (scenario.get("extra_metadata") or {}).get("pair_key")
        if pair:
            groups.setdefault(f"pair_key={pair}", []).append(conv)
        case = scenario.get("data_quality_case_id")
        if case:
            groups.setdefault(f"data_quality_case_id={case}", []).append(conv)
    return {name: members for name, members in groups.items() if len(members) > 1}


def build_hw5_labels(mode: str = MODE) -> dict[str, Any]:
    """Write ``hw5_labels/<mode>.jsonl`` (Pass=1) from HW4 labels plus HW5 candidates.

    Only traces kept as judge inputs are written, so every label has a matching
    record. Re-run after adding new labels: the Homework 5 file must stay the
    complete set, because the helpers stop reading the Homework 4 file once it
    exists.
    """
    records = json.loads(INPUTS.read_text(encoding="utf-8"))
    eligible = {r["trace_id"]: r["conv_id"] for r in records}
    hw4 = _hw4_labels(mode)
    candidates = _candidate_labels(mode)
    relabels = _hw4_relabels(mode)

    out = STATE / "hw5_labels" / f"{mode}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    counts = {"pass": 0, "fail": 0}
    for trace_id, conv_id in eligible.items():
        if trace_id in candidates:
            row = candidates[trace_id]
            pass_label = 1 if row["verdict"] == "pass" else 0
            entry = {"trace_id": trace_id, "conv_id": conv_id, "label": pass_label,
                     "source": "hw5-candidate", "note": row.get("note", ""),
                     "ts": row.get("ts")}
        elif conv_id in relabels:
            row = relabels[conv_id]
            pass_label = 1 if row["verdict"] == "pass" else 0
            entry = {"trace_id": trace_id, "conv_id": conv_id, "label": pass_label,
                     "source": "hw5-recheck", "was": row.get("was"),
                     "note": row.get("reason", ""), "ts": row.get("ts")}
        elif trace_id in hw4:
            row = hw4[trace_id]
            pass_label = 1 - int(row["label"])  # HW4 1 = failure present
            entry = {"trace_id": trace_id, "conv_id": conv_id, "label": pass_label,
                     "source": "hw4-converted", "ts": row.get("ts")}
        else:
            continue
        counts["pass" if pass_label else "fail"] += 1
        lines.append(json.dumps(entry))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {**counts, "written": len(lines), "path": str(out)}


# ---------------------------------------------------------------------------
# Part B: splits
# ---------------------------------------------------------------------------


def split_data(mode: str = MODE, seed: int = 7) -> dict[str, Any]:
    """Split labels 20 / 40 / 40 over the eligible judge-input traces.

    Run once. ``analysis/state/splits.json`` stays fixed through development.
    """
    from analysis.helpers import split_labels

    records = json.loads(INPUTS.read_text(encoding="utf-8"))
    splits = split_labels(
        mode,
        fractions=(0.20, 0.40, 0.40),
        seed=seed,
        min_per_class=10,
        eligible_trace_ids=[r["trace_id"] for r in records],
    )
    labels = {
        json.loads(line)["trace_id"]: json.loads(line)["label"]
        for line in (STATE / "hw5_labels" / f"{mode}.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    }
    return {
        name: {
            "n": len(ids),
            "pass": sum(labels.get(i, 0) == 1 for i in ids),
            "fail": sum(labels.get(i, 1) == 0 for i in ids),
        }
        for name, ids in splits.items()
    }


# ---------------------------------------------------------------------------
# Part C / D: development and the frozen test
# ---------------------------------------------------------------------------


def _require_trace_source() -> None:
    """Point the helpers at the saved inputs and load the model key from ``.env``.

    The course helpers read ``OPENAI_API_KEY`` from the environment but never load
    ``.env`` themselves, so a key kept only in ``.env`` would silently leave the
    judge with no backend.
    """
    os.environ.setdefault("CARTWHEEL_JUDGE_TRACE_SOURCE", str(INPUTS))
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO / ".env")


def run_development(
    mode: str = MODE, prompt_path: str | Path | None = None, batch_size: int = 10
) -> dict[str, Any]:
    """Register a prompt version, run it on dev, and save the metrics."""
    from analysis.helpers import judge_alignment, register_judge, run_judge

    _require_trace_source()
    prompt_path = Path(prompt_path or REPO / "analysis" / "prompts" / f"{mode}-v0.txt")
    record = register_judge(
        mode=mode,
        prompt_text=prompt_path.read_text(encoding="utf-8"),
        judge_model=JUDGE_MODEL,
    )
    judge_id = record["judge_id"]
    run_judge(judge_id, split="dev", batch_size=batch_size)
    metrics = judge_alignment(judge_id, split="dev")

    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / f"dev-{judge_id}.json").write_text(
        json.dumps({"prompt_path": str(prompt_path), **metrics}, indent=1),
        encoding="utf-8",
    )
    return {"judge_id": judge_id, **metrics}


def run_test(judge_id: str, batch_size: int = 10) -> dict[str, Any]:
    """Freeze the chosen judge and score it once on the held-out test split."""
    from analysis.helpers import freeze_judge, judge_alignment, run_judge

    _require_trace_source()
    freeze_judge(judge_id)
    run_judge(judge_id, split="test", batch_size=batch_size)
    metrics = judge_alignment(judge_id, split="test")

    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / f"test-{judge_id}.json").write_text(
        json.dumps(metrics, indent=1), encoding="utf-8"
    )
    return metrics


# Earlier name, kept so existing notes and commands still work.
convert_hw4_labels = build_hw5_labels


# ---------------------------------------------------------------------------
# Part E: recompute metrics from saved predictions (no model calls)
# ---------------------------------------------------------------------------


def recompute_metrics(judge_id: str = "ungrounded_claim-v3", split: str = "test") -> dict[str, Any]:
    """Recompute TPR, TNR and Wilson intervals from saved predictions only.

    Made for the video: it reads the judge record, the HW5 labels and the split,
    and never calls a model. ``judge_alignment`` refuses to score a split with a
    missing prediction; here a trace with no valid judge output is reported
    separately instead of being given an invented verdict.
    """
    from analysis.helpers.tools import _wilson_interval

    judge = json.loads((STATE / "judges" / f"{judge_id}.json").read_text(encoding="utf-8"))
    preds = judge["predictions"][judge["prompt_hash"]]        # Pass = 1 (pass_positive)
    labels = {}
    for line in (STATE / "hw5_labels" / f"{judge['mode']}.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            labels[row["trace_id"]] = row                    # Pass = 1
    ids = json.loads((STATE / "splits.json").read_text(encoding="utf-8"))[judge["mode"]][split]
    tp = fn = tn = fp = 0
    for tid in ids:
        if tid not in preds:
            continue
        human, pred = labels[tid]["label"], int(preds[tid])
        if human == 1:
            tp, fn = (tp + 1, fn) if pred == 1 else (tp, fn + 1)
        else:
            tn, fp = (tn + 1, fp) if pred == 0 else (tn, fp + 1)
    return {
        "judge_id": judge_id, "status": judge.get("status"), "model": judge["model"], "split": split,
        "class_counts": {"pass": sum(labels[t]["label"] == 1 for t in ids),
                         "fail": sum(labels[t]["label"] == 0 for t in ids)},
        "tp": tp, "fn": fn, "tn": tn, "fp": fp,
        "tpr": round(tp / (tp + fn), 4) if tp + fn else None,
        "tnr": round(tn / (tn + fp), 4) if tn + fp else None,
        "tpr_interval": _wilson_interval(tp, tp + fn),
        "tnr_interval": _wilson_interval(tn, tn + fp),
        "no_valid_output": [labels[t]["conv_id"] for t in ids if t not in preds],
    }


if __name__ == "__main__":
    print(json.dumps(recompute_metrics(), indent=1))
