"""Run the Homework 7 monitor for one configured period or the last N hours.

    uv run python -m monitoring.run --period before
    uv run python -m monitoring.run --period after
    uv run python -m monitoring.run --last-hours 24      # the scheduled job

A period run fetches the traces in the configured UTC window, keeps the
monitoring scenarios, and requires exactly one conversation per scenario on the
configured Cartwheel model. A ``--last-hours`` run groups whatever traces the
window holds by ``meta.session_id``; an empty window records a zero count and
exits without calling the judge.

Each conversation record joins its traces in time order into the normalized
Homework 5 text (ordered turns plus tool calls and results) and uses the final
trace id as its id, so Langfuse can receive the score. Risk groups are built
from the tools the traces actually called and the number of user turns, never
from the agent's reply.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from analysis.helpers.normalization import _flatten, normalize_trace
from monitoring.chart import prevalence_chart
from monitoring.correct import corrected_mode_prevalence
from monitoring.run_judges import judge_sample, judge_test_data, load_monitoring_judge
from monitoring.sample import DEFAULT_RISK_GROUPS, select_traces
from monitoring.write_scores import build_score_records, post_scores

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "monitoring" / "config.json"
SCENARIOS_PATH = ROOT / "scenarios" / "monitoring_scenarios.jsonl"
HISTORY_PATH = ROOT / "monitoring" / "history.jsonl"
CHART_PATH = ROOT / "monitoring" / "prevalence.svg"
OUTPUT_DIR = ROOT / "monitoring" / "output"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def scenario_ids() -> list[str]:
    lines = SCENARIOS_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["id"] for line in lines if line.strip()]


def fetch_window(start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]]:
    """Every Langfuse trace in [start, end), normalized."""
    from langfuse import Langfuse

    client = Langfuse()
    summaries: list[Any] = []
    page = 1
    while True:
        response = client.api.trace.list(
            from_timestamp=start, to_timestamp=end, limit=100, page=page
        )
        batch = list(response.data or [])
        summaries.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return [normalize_trace(client.api.trace.get(summary.id)) for summary in summaries]


def build_conversations(
    traces: list[dict[str, Any]], key: str
) -> list[dict[str, Any]]:
    """Join each group's traces, in time order, into one conversation record."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        group_id = trace["meta"].get(key)
        if group_id:
            groups[str(group_id)].append(trace)

    conversations = []
    for group_id, members in groups.items():
        members.sort(key=lambda t: (t.get("timestamp") or "", t["id"]))
        messages = [message for trace in members for message in trace["trace"]]
        conversations.append(
            {
                "id": members[-1]["id"],
                "group_id": group_id,
                "scenario_id": members[-1]["meta"].get("scenario_id"),
                "trace_ids": [trace["id"] for trace in members],
                "models": sorted({m for trace in members for m in trace["models"]}),
                "tools": sorted(
                    {
                        str(message["name"])
                        for message in messages
                        if message.get("role") == "tool_call" and message.get("name")
                    }
                ),
                "turn_count": sum(message.get("role") == "user" for message in messages),
                "timestamp": members[-1].get("timestamp") or "",
                "text": _flatten(messages),
            }
        )
    conversations.sort(key=lambda c: c["group_id"])
    return conversations


def validate_period(
    conversations: list[dict[str, Any]], expected_ids: list[str], model: str
) -> None:
    """Reject a period that is incomplete, has retries, or used another model."""
    found = [c["scenario_id"] for c in conversations]
    missing = sorted(set(expected_ids) - set(found))
    if missing:
        raise ValueError(f"period is missing {len(missing)} scenario ids, e.g. {missing[:5]}")
    if len(conversations) != len(expected_ids):
        raise ValueError(
            f"expected {len(expected_ids)} conversations, found {len(conversations)}"
        )
    wrong = [
        c["scenario_id"] for c in conversations if c["models"] != [model]
    ]
    if wrong:
        raise ValueError(f"{len(wrong)} conversations used a model other than {model}: {wrong[:5]}")


def plan_summary(plan: dict[str, Any]) -> str:
    groups = ", ".join(f"{name} {len(items)}" for name, items in plan["risk_groups"].items())
    return (
        f"random sample {len(plan['random'])}; risk groups: {groups}; "
        f"unique conversations to judge (= judge calls): {len(plan['to_judge'])}"
    )


def append_history(record: dict[str, Any]) -> None:
    """Keep one line per label: a rerun of a period replaces its earlier line."""
    rows = []
    if HISTORY_PATH.exists():
        rows = [
            json.loads(line)
            for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    rows = [row for row in rows if row.get("label") != record["label"]] + [record]
    HISTORY_PATH.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    points = [row for row in rows if row.get("corrected") is not None]
    if points:
        config = load_config()
        CHART_PATH.write_text(
            prevalence_chart(points, config["threshold"], config["judge_mode"]),
            encoding="utf-8",
        )


def run(label: str, start: dt.datetime, end: dt.datetime, *, period: bool, yes: bool) -> int:
    config = load_config()
    judge = load_monitoring_judge(config["judge_id"])
    if judge["mode"] != config["judge_mode"]:
        raise ValueError("config judge_mode does not match the frozen judge")
    mode = judge["mode"]

    traces = fetch_window(start, end)
    if period:
        expected = scenario_ids()
        traces = [t for t in traces if t["meta"].get("scenario_id") in set(expected)]
        conversations = build_conversations(traces, "scenario_id")
        validate_period(conversations, expected, config["model"])
    else:
        conversations = build_conversations(traces, "session_id")
        conversations = [c for c in conversations if config["model"] in c["models"]]

    base = {
        "label": label,
        "from": start.isoformat().replace("+00:00", "Z"),
        "to": end.isoformat().replace("+00:00", "Z"),
        "judge_id": config["judge_id"],
        "model": config["model"],
        "trace_count": sum(len(c["trace_ids"]) for c in conversations),
        "conversation_count": len(conversations),
    }
    print(f"{label}: {base['trace_count']} traces -> {len(conversations)} conversations")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{label}.json"
    if not conversations:
        record = {**base, "random_count": 0, "risk_count": 0, "raw": None, "corrected": None}
        out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        if period:
            append_history(record)
        print("no eligible conversations; the judge was not called")
        return 0

    risk_groups = {name: DEFAULT_RISK_GROUPS[name] for name in config["risk_groups"]}
    plan = select_traces(conversations, config["random_rate"], risk_groups)
    print(plan_summary(plan))
    if not yes:
        answer = input(f"Call judge {config['judge_id']} ({judge['model']}) {len(plan['to_judge'])} times? [y/N] ")
        if answer.strip().lower() != "y":
            print("stopped before calling the judge")
            return 1

    verdicts = judge_sample(
        config["judge_id"], [{"id": c["id"], "text": c["text"]} for c in plan["to_judge"]]
    )
    random_verdicts = {c["id"]: verdicts[c["id"]] for c in plan["random"]}
    risk_ids = list(dict.fromkeys(c["id"] for items in plan["risk_groups"].values() for c in items))
    risk_verdicts = {trace_id: verdicts[trace_id] for trace_id in risk_ids}

    test_labels, test_preds = judge_test_data(config["judge_id"])
    estimate = corrected_mode_prevalence(list(random_verdicts.values()), test_labels, test_preds)

    records = build_score_records(mode, random_verdicts, risk_verdicts, estimate, label)
    # Date each score by the traffic it measures, so a dashboard plots the
    # periods at their own times rather than at the time the monitor ran.
    by_id = {c["id"]: c for c in conversations}
    for score in records:
        conversation = by_id.get(score["trace_id"]) if score["trace_id"] else None
        stamp = conversation["timestamp"] if conversation else ""
        score["timestamp"] = parse_utc(stamp) if stamp else end
    written = post_scores(records)

    record = {
        **base,
        "random_count": len(random_verdicts),
        "risk_count": len(risk_verdicts),
        "risk_group_counts": {name: len(items) for name, items in plan["risk_groups"].items()},
        "judge_calls": len(plan["to_judge"]),
        "raw": estimate["raw"],
        "corrected": estimate["corrected"],
        "ci_low": estimate["ci_low"],
        "ci_high": estimate["ci_high"],
        "failure_sensitivity": estimate["failure_sensitivity"],
        "pass_specificity": estimate["pass_specificity"],
        "threshold": config["threshold"],
        "crossed_threshold": estimate["corrected"] > config["threshold"],
    }
    detail = {
        **record,
        "random": [
            {"trace_id": i, "scenario_id": by_id[i]["scenario_id"], "verdict": v}
            for i, v in random_verdicts.items()
        ],
        "risk": [
            {
                "trace_id": i,
                "scenario_id": by_id[i]["scenario_id"],
                "groups": [n for n, items in plan["risk_groups"].items() if any(c["id"] == i for c in items)],
                "verdict": v,
            }
            for i, v in risk_verdicts.items()
        ],
        "scores_written": written,
    }
    out_path.write_text(json.dumps(detail, indent=2), encoding="utf-8")
    if period:
        append_history(record)
    print(
        f"raw {estimate['raw']}, corrected {estimate['corrected']} "
        f"(95% CI {estimate['ci_low']}-{estimate['ci_high']}); {written} scores written"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--period", help="a period label from monitoring/config.json")
    which.add_argument("--last-hours", type=float, help="monitor the traces of the last N hours")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation before judge calls")
    args = parser.parse_args(argv)

    from observability.instrument import load_env

    load_env()
    if args.period:
        periods = {p["label"]: p for p in load_config()["periods"]}
        if args.period not in periods:
            parser.error(f"unknown period {args.period!r}; choose from {sorted(periods)}")
        chosen = periods[args.period]
        return run(args.period, parse_utc(chosen["from"]), parse_utc(chosen["to"]), period=True, yes=args.yes)

    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(hours=args.last_hours)
    label = f"daily-{end:%Y-%m-%d}"
    return run(label, start, end, period=False, yes=args.yes)


if __name__ == "__main__":
    sys.exit(main())
