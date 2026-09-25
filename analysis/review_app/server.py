"""Review app server for HW4 error analysis (stdlib only, no web framework).

Serves the review UI and reads/writes the analysis state files:

    GET  /                       the app (index.html)
    GET  /api/conversations      conversations built by build_data.py
    GET  /api/manifest           analysis/state/sample_manifest.json
    POST /api/manifest           replace the manifest (batches and their items)
    GET  /api/annotations        analysis/state/annotations.json
    POST /api/annotations        replace annotations (the app posts on every change)
    GET  /api/patterns           analysis/state/patterns.json (the taxonomy)
    POST /api/patterns           replace the taxonomy
    GET  /api/suggestions        analysis/state/suggestions.json
    POST /api/suggestions        replace suggestions (accept/reject decisions)
    GET  /api/labels             latest present/absent label per (conversation, mode)
    POST /api/labels             record one label; appends to labels/<mode>.jsonl
                                 and writes a Langfuse score on every trace in the
                                 conversation

Labels are stored against Langfuse trace ids. A multi-turn conversation has one
trace per turn, and each turn trace receives the same score, so a Langfuse
filter on any turn finds the label. Scores use a deterministic score id per
(trace, mode), so changing a label overwrites the score instead of adding one.

Usage:
    uv run python analysis/review_app/build_data.py     # once, or after new traces
    uv run python analysis/review_app/server.py          # http://localhost:8765
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # the app still works offline without python-dotenv
    pass

from analysis.helpers import langfuse_io  # noqa: E402
from analysis.helpers._state import (  # noqa: E402
    append_jsonl,
    read_json,
    read_jsonl,
    state_path,
    write_json,
)

def _judge_view(judge_id: str | None) -> dict[str, Any]:
    """Human label, judge verdict and critique side by side, for the Judge tab.

    Reads only saved files (HW5 labels, splits, judge records); never calls a model.
    Labels and predictions both use Pass = 1. A split's predictions appear only if
    that split has been run, so test rows stay empty until the judge is frozen and
    tested.
    """
    judges_dir = state_path("judges")
    records = {}
    for p in sorted(judges_dir.glob("*-v*.json")):
        rec = read_json(p, {})
        has_labels = state_path("hw5_labels", f"{rec.get('mode')}.jsonl").exists()
        if rec.get("judge_id") and rec.get("label_convention") == "pass_positive" and has_labels:
            records[rec["judge_id"]] = rec
    if not records:
        return {"judges": [], "rows": []}
    frozen = [j for j, r in records.items() if r.get("status") == "frozen"]
    judge_id = judge_id if judge_id in records else (frozen[0] if frozen else sorted(records)[-1])
    rec = records[judge_id]
    mode = rec["mode"]
    labels = {r["trace_id"]: r for r in read_jsonl(state_path("hw5_labels", f"{mode}.jsonl"))}
    splits = read_json(state_path("splits.json"), {}).get(mode, {})
    preds = rec.get("predictions", {}).get(rec["prompt_hash"], {})
    crits = rec.get("critiques", {}).get(rec["prompt_hash"], {})
    rows = []
    for split in ("train", "dev", "test"):
        for tid in splits.get(split, []):
            lab = labels.get(tid, {})
            human = "Pass" if lab.get("label") == 1 else "Fail" if lab.get("label") == 0 else None
            judge = None if tid not in preds else ("Pass" if int(preds[tid]) == 1 else "Fail")
            rows.append({
                "trace_id": tid, "conv_id": lab.get("conv_id"), "split": split,
                "human": human, "judge": judge, "critique": crits.get(tid, ""),
                "note": lab.get("note", ""), "disagree": judge is not None and human is not None and judge != human,
            })
    judges = [{"judge_id": j, "status": r.get("status"), "model": r.get("model")} for j, r in sorted(records.items())]
    return {"judge_id": judge_id, "status": rec.get("status"), "model": rec.get("model"),
            "mode": mode, "judges": judges, "rows": rows}


APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "data" / "conversations.json"

FILES = {
    "/api/manifest": (state_path("sample_manifest.json"), {"batches": []}),
    "/api/annotations": (state_path("annotations.json"), {"annotations": []}),
    "/api/patterns": (state_path("patterns.json"), {"modes": []}),
    "/api/suggestions": (state_path("suggestions.json"), []),
}
LABEL_DIR = state_path("labels")
_lock = threading.Lock()
_lf_client: Any = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _langfuse() -> Any | None:
    """A cached Langfuse client, or None when the env is not configured."""
    global _lf_client
    if not langfuse_io.is_configured():
        return None
    if _lf_client is None:
        from langfuse import Langfuse

        _lf_client = Langfuse()
    return _lf_client


def _score_id(trace_id: str, mode: str) -> str:
    return hashlib.sha256(f"{trace_id}:{mode}".encode()).hexdigest()[:32]


def _current_modes() -> set[str]:
    """Modes that can be labelled: the final ones once any are marked final."""
    data = read_json(FILES["/api/patterns"][0], {"modes": []})
    modes = [m for m in data.get("modes", []) if m.get("name")]
    final = [m for m in modes if m.get("status") == "final"]
    return {m["name"] for m in (final or modes) if m.get("status") != "rejected"}


def _read_labels() -> dict[str, dict[str, dict[str, Any]]]:
    """Latest label per conversation and mode, for modes in the taxonomy only.

    The label files are append-only logs; the last record wins. Files for modes
    not in patterns.json (for example the course demo file) are ignored.
    """
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for mode in _current_modes():
        for rec in read_jsonl(LABEL_DIR / f"{mode}.jsonl"):
            conv = rec.get("conv_id")
            if not conv:
                continue
            out.setdefault(conv, {})[mode] = {
                "label": rec.get("label"),
                "ts": rec.get("ts"),
                "langfuse": rec.get("langfuse"),
            }
    return out


def _write_label(body: dict[str, Any]) -> dict[str, Any]:
    conv_id = body["conv_id"]
    mode = body["mode"]
    label = body.get("label")
    trace_ids = body.get("trace_ids") or []
    if label not in (0, 1, None):
        raise ValueError("label must be 0, 1, or null (cleared)")
    if mode not in _current_modes():
        raise ValueError(f"unknown mode {mode!r}; add it to the taxonomy first")

    status = "skipped"
    if label is not None:
        lf = _langfuse()
        if lf is None:
            status = "not_configured"
        else:
            try:
                for tid in trace_ids:
                    lf.create_score(
                        name=mode,
                        value=int(label),
                        trace_id=tid,
                        score_id=_score_id(tid, mode),
                        data_type="NUMERIC",
                        comment=f"{conv_id}: human label ({'present' if label else 'absent'})",
                    )
                lf.flush()
                status = "written"
            except Exception as exc:  # keep the local record even if Langfuse fails
                status = f"error: {exc}"

    ts = _now()
    with _lock:
        for i, tid in enumerate(trace_ids or [conv_id]):
            append_jsonl(
                LABEL_DIR / f"{mode}.jsonl",
                {
                    "trace_id": tid,
                    "conv_id": conv_id,
                    "turn": i + 1,
                    "label": label,
                    "source": "human",
                    "ts": ts,
                    "label_id": f"{tid}#{mode}#{ts}",
                    "langfuse": status,
                },
            )
    return {"ok": True, "langfuse": status, "ts": ts}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        if "/api/" in (args[0] if args else "") and "POST" in (args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _body(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "null")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send((APP_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/conversations":
            if not DATA_FILE.exists():
                self._json({"error": "run analysis/review_app/build_data.py first"}, 500)
                return
            self._send(DATA_FILE.read_bytes(), "application/json; charset=utf-8")
        elif path in FILES:
            file, default = FILES[path]
            self._json(read_json(file, default))
        elif path == "/api/labels":
            self._json({"labels": _read_labels(), "langfuse": langfuse_io.is_configured()})
        elif path == "/api/proposed":
            # agent-proposed labels derived from open codes; never saved as labels
            # until the reviewer accepts them in the Labels view
            self._json(read_json(state_path("proposed_labels.json"), {}))
        elif path == "/api/judge":
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(self.path).query)
            self._json(_judge_view(query.get("judge", [None])[0]))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        try:
            body = self._body()
            if path in FILES:
                with _lock:
                    write_json(FILES[path][0], body)
                self._json({"ok": True, "ts": _now()})
            elif path == "/api/labels":
                self._json(_write_label(body))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 400)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    lf = "on" if langfuse_io.is_configured() else "off (labels saved locally only)"
    print(f"review app: http://localhost:{args.port}   Langfuse scores: {lf}")
    server.serve_forever()


if __name__ == "__main__":
    main()
