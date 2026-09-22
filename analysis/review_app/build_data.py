"""Build the review app's conversation file from the exported Langfuse traces.

Cartwheel writes one Langfuse trace per user turn. This script groups traces by
``cartwheel.session_id``, orders the turns by timestamp, and rebuilds each turn
from its observations in start-time order (user message, tool calls with
results, final reply). It joins the scenario's tuple and extra metadata by
scenario id, computes a few structural features, flags outliers, clusters the
conversations for the map view, and writes the first review batch
(15 uniform random + 15 cluster representatives) to the sample manifest.

Usage:
    uv run python analysis/review_app/build_data.py
    uv run python analysis/review_app/build_data.py --traces traces/support_traces.json

The conversation file is derived data and is not committed. The manifest is
only written when it does not exist yet, so rebuilding never reshuffles
batches you have already started.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from analysis.helpers._state import read_json, state_path, write_json  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "data" / "conversations.json"
MANIFEST = state_path("sample_manifest.json")

WRITE_TOOLS = {"issue_refund", "cancel_order", "escalate_to_human"}
SEED = 20260919
N_CLUSTERS = 8


def _text(parts: Any) -> str:
    """Pull plain text out of an OTel message list ``[{parts:[{content}]}]``."""
    if parts is None:
        return ""
    if isinstance(parts, str):
        return parts
    out: list[str] = []
    for msg in parts if isinstance(parts, list) else [parts]:
        if isinstance(msg, dict):
            for part in msg.get("parts") or []:
                if isinstance(part, dict) and part.get("content"):
                    out.append(str(part["content"]))
            if msg.get("content") and not msg.get("parts"):
                out.append(str(msg["content"]))
    return "\n".join(out)


def _tool_error(output: Any) -> str | None:
    """Return a short reason when a tool result is an error or empty."""
    if not isinstance(output, dict):
        return None
    if output.get("ok") is False or output.get("error"):
        return str(output.get("error") or output.get("reason") or "error")
    for key in ("results", "orders", "products"):
        if key in output and isinstance(output[key], list) and not output[key]:
            return f"empty {key}"
    if output.get("count") == 0:
        return "no results"
    return None


def _turn(trace: dict[str, Any]) -> dict[str, Any]:
    obs = sorted(trace.get("observations") or [], key=lambda o: o.get("startTime") or "")
    session = next((o for o in obs if o.get("name") == "cartwheel.session_message"), None)
    user = _text(session.get("input") if session else trace.get("input"))
    answer = _text(session.get("output") if session else trace.get("output"))
    steps = []
    for o in obs:
        if o.get("type") != "TOOL":
            continue
        err = _tool_error(o.get("output"))
        steps.append(
            {
                "tool": o.get("name"),
                "input": o.get("input"),
                "output": o.get("output"),
                "error": err,
                "write": o.get("name") in WRITE_TOOLS,
            }
        )
    gen = next((o for o in obs if o.get("type") == "GENERATION"), None)
    system = ""
    model = None
    if gen:
        model = gen.get("model")
        msgs = gen.get("input") or []
        if msgs and isinstance(msgs[0], dict) and msgs[0].get("role") == "system":
            system = _text([msgs[0]])
    return {
        "trace_id": trace["id"],
        "timestamp": trace.get("timestamp"),
        "latency": trace.get("latency"),
        "user": user,
        "steps": steps,
        "answer": answer,
        "system": system,
        "model": model,
    }


def build(traces_path: Path, scenarios_path: Path) -> list[dict[str, Any]]:
    raw = json.loads(traces_path.read_text(encoding="utf-8"))
    traces = raw["traces"] if isinstance(raw, dict) else raw
    scenarios = {
        r["id"]: r
        for r in (
            json.loads(line)
            for line in scenarios_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }

    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in traces:
        attrs = (t.get("metadata") or {}).get("attributes") or t.get("metadata") or {}
        sid = attrs.get("cartwheel.session_id") or t.get("sessionId") or t["id"]
        by_session[sid].append(t)

    convs = []
    for sid, group in by_session.items():
        group.sort(key=lambda t: t.get("timestamp") or "")
        attrs = (group[0].get("metadata") or {}).get("attributes") or {}
        scen_ids = {
            ((t.get("metadata") or {}).get("attributes") or {}).get("cartwheel.scenario_id")
            for t in group
        }
        scen_id = attrs.get("cartwheel.scenario_id")
        if len(scen_ids) != 1:
            print(f"warning: session {sid} spans scenarios {scen_ids}", file=sys.stderr)
        scen = scenarios.get(scen_id, {})
        turns = [_turn(t) for t in group]
        convs.append(
            {
                "id": scen_id or sid,
                "session_id": sid,
                "trace_ids": [t["id"] for t in group],
                "role": attrs.get("cartwheel.user_role"),
                "user_id": attrs.get("cartwheel.user_id"),
                "prompt_version": attrs.get("cartwheel.prompt_version"),
                "model": next((t["model"] for t in turns if t["model"]), None),
                "group": scen.get("scenario_group"),
                "dq_case": scen.get("data_quality_case_id"),
                "tuple": scen.get("tuple"),
                "expected": scen.get("expected"),
                "extra": scen.get("extra_metadata"),
                "turns": turns,
            }
        )

    # The system prompt embeds the user's context, so no two prompts match
    # exactly. Lines present in nearly every prompt are boilerplate; the rest
    # (role, user id, store id) is what the reviewer needs to see.
    prompts = [t["system"] for c in convs for t in c["turns"] if t["system"]]
    line_counts: dict[str, int] = defaultdict(int)
    for p in prompts:
        for line in set(p.splitlines()):
            line_counts[line] += 1
    common = {line for line, n in line_counts.items() if n >= 0.9 * len(prompts)}
    prefix = max(prompts, key=lambda p: sum(line in common for line in p.splitlines())) if prompts else ""
    for c in convs:
        for t in c["turns"]:
            t["system_specific"] = [
                line for line in t["system"].splitlines() if line.strip() and line not in common
            ]
            del t["system"]
    _features(convs)
    _flags(convs)
    _cluster(convs)
    convs.sort(key=lambda c: c["id"])
    return convs, prefix


def _features(convs: list[dict[str, Any]]) -> None:
    for c in convs:
        steps = [s for t in c["turns"] for s in t["steps"]]
        c["features"] = {
            "turns": len(c["turns"]),
            "tool_calls": len(steps),
            "distinct_tools": len({s["tool"] for s in steps}),
            "tool_errors": sum(1 for s in steps if s["error"]),
            "write_calls": sum(1 for s in steps if s["write"]),
            "answer_chars": sum(len(t["answer"]) for t in c["turns"]),
            "latency": round(sum(t["latency"] or 0 for t in c["turns"]), 2),
        }


def _flags(convs: list[dict[str, Any]]) -> None:
    """Flag a record only when it sits in the top or bottom 5% of a feature."""
    labels = {
        "tool_calls": "tool calls",
        "answer_chars": "characters in reply",
        "latency": "seconds",
    }
    for key, label in labels.items():
        vals = np.array([c["features"][key] for c in convs], dtype=float)
        for c in convs:
            v = c["features"][key]
            above = float((vals < v).mean())
            below = float((vals > v).mean())
            c.setdefault("flags", [])
            if above >= 0.95:
                c["flags"].append(f"{v:g} {label} (more than {min(99, int(above * 100))}%)")
            elif below >= 0.95 and key != "tool_calls":
                c["flags"].append(f"{v:g} {label} (fewer than {min(99, int(below * 100))}%)")


def _cluster(convs: list[dict[str, Any]]) -> None:
    roles = sorted({c["role"] for c in convs})
    X = []
    for c in convs:
        f = c["features"]
        X.append(
            [
                f["turns"],
                f["tool_calls"],
                f["distinct_tools"],
                f["tool_errors"],
                min(f["write_calls"], 1),
                np.log1p(f["answer_chars"]),
            ]
            + [1.0 if c["role"] == r else 0.0 for r in roles]
        )
    X = np.array(X, dtype=float)
    X = (X - X.mean(0)) / np.where(X.std(0) == 0, 1, X.std(0))
    rng = np.random.default_rng(SEED)
    centers = X[rng.choice(len(X), N_CLUSTERS, replace=False)]
    for _ in range(100):
        assign = ((X[:, None, :] - centers[None]) ** 2).sum(-1).argmin(1)
        new = np.array(
            [X[assign == k].mean(0) if (assign == k).any() else centers[k] for k in range(N_CLUSTERS)]
        )
        if np.allclose(new, centers):
            break
        centers = new
    dist = np.sqrt(((X - centers[assign]) ** 2).sum(-1))
    # 2D projection for the map (PCA via SVD)
    U, S, _ = np.linalg.svd(X - X.mean(0), full_matrices=False)
    xy = U[:, :2] * S[:2]
    for i, c in enumerate(convs):
        c["cluster"] = int(assign[i])
        c["centroid_dist"] = round(float(dist[i]), 4)
        c["xy"] = [round(float(xy[i, 0]), 4), round(float(xy[i, 1]), 4)]


def first_batch(convs: list[dict[str, Any]]) -> dict[str, Any]:
    """15 uniform random + 15 cluster representatives, no overlap."""
    rng = random.Random(SEED)
    rand = rng.sample(convs, 15)
    taken = {c["id"] for c in rand}
    by_cluster: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for c in sorted(convs, key=lambda c: c["centroid_dist"]):
        if c["id"] not in taken:
            by_cluster[c["cluster"]].append(c)
    reps: list[dict[str, Any]] = []
    depth = 0
    while len(reps) < 15:
        for k in sorted(by_cluster):
            if depth < len(by_cluster[k]) and len(reps) < 15:
                reps.append(by_cluster[k][depth])
        depth += 1
    items = [
        {"conv_id": c["id"], "trace_ids": c["trace_ids"], "reason": "uniform random"}
        for c in rand
    ] + [
        {
            "conv_id": c["id"],
            "trace_ids": c["trace_ids"],
            "reason": f"cluster {c['cluster']} representative",
        }
        for c in reps
    ]
    return {"name": "batch1", "title": "Random + cluster", "strategy": "15 uniform random + 15 cluster representatives", "items": items}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", default=str(ROOT / "traces" / "support_traces.json"))
    ap.add_argument("--scenarios", default=str(ROOT / "scenarios" / "support_scenarios.jsonl"))
    args = ap.parse_args()
    convs, prefix = build(Path(args.traces), Path(args.scenarios))
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "system_prompt_common": prefix,
        "conversations": convs,
    }
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(convs)} conversations to {DATA_FILE.relative_to(ROOT)}")

    manifest = read_json(MANIFEST, None)
    if not manifest or not manifest.get("batches"):
        manifest = {
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": str(Path(args.traces).relative_to(ROOT)) if Path(args.traces).is_relative_to(ROOT) else args.traces,
            "batches": [
                first_batch(convs),
                {"name": "batch2", "title": "One dimension", "strategy": "", "items": []},
                {"name": "batch3", "title": "Depth search", "strategy": "depth searches for candidate modes and close negatives", "items": []},
                {"name": "batch4", "title": "Final random", "strategy": "15 uniform random after drafting the taxonomy", "items": []},
            ],
        }
        write_json(MANIFEST, manifest)
        print(f"wrote first batch to {MANIFEST.relative_to(ROOT)}")
    else:
        print("sample manifest already exists; left unchanged")


if __name__ == "__main__":
    main()
