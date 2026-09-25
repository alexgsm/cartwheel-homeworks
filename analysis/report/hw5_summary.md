# Homework 5 — LLM judge for `ungrounded_claim`

## Failure mode

**`ungrounded_claim`**: the agent states a fact, policy, capability or outcome that nothing in the conversation
backs up. Chosen because it had the most failures in Homework 4 and cannot be checked with code: deciding whether
a sentence is supported by a tool result needs reading.

Criterion (true = Pass):

> Every fact, policy, capability or outcome in the agent's replies is backed up by a tool result or by words the
> user wrote in the trace, except (1) promises that a human may grant an exception, (2) claims about which order
> the user means, and (3) offers to look something up next in this chat.

Full definition, evidence rules and boundaries: [`hw5_part_a.md`](hw5_part_a.md).

## Labels, inputs, split

| | |
|---|---|
| Labelled conversations | **112**: 65 Pass, 47 Fail |
| Sources | 77 Homework 4 labels (converted to Pass = 1), 23 new enrich candidates, 12 Homework 4 labels changed in the recheck |
| Exclusions | 11 close scenario variants, all Pass (`analysis/state/hw5_exclusions.json`) |
| Split (20/40/40, seed 7, run once) | train 13P/9F · dev 26P/19F · test 26P/19F |
| Judge input | whole conversation per record (both turns), tool calls and results included, no labels or notes; 221 tool calls verified through `normalize_trace` |

Two things changed the labelled set before the split, both recorded in `hw5_part_a.md`:
- The definition was sharpened while labelling candidates (tool definitions count as support; offers to look
  something up are excused; made-up reasons, hedged guesses and details around an exception still fail), so every
  kept Homework 4 label was rechecked against it: 12 changed, all Pass → Fail, each with a written reason.
- The recheck found a bug in my first `prepare_inputs()`: it kept only the first turn of two-turn conversations,
  while several labels rest on the second turn. Records now hold the whole conversation.

## Judge versions (gpt-4o-mini, dev = 45 traces)

| Version | What changed | Dev TPR | Dev TNR | Notes |
|---|---|---|---|---|
| v0 | first draft: criterion, support rules, Pass/Fail lists, 3 train examples (0222 Fail, 0079 Pass, 0170 borderline Pass) | — | — | stopped on the first batch: DocETL could not parse a verdict (`'Not found'`) |
| v1 | format only: explicit one-word result | — | — | same parse failure on other traces; cause was over-long critiques, not wording |
| v2 | format only: short critique, no JSON in the critique | 0.04 [0.01, 0.19] | 0.95 [0.75, 0.99] | first complete run; judge fails almost every good reply |
| v3 | **revision 1**: default Pass, re-check the tool result, Pass rules for questions / tool-result descriptions / policy paraphrase / small refunds / cancel vs refund eligibility, narrower escalation rule, read every turn, 4th train example (0096) | 0.08 [0.02, 0.24] | 1.00 [0.83, 1.00] | 24 false alarms |
| v4 | **revision 2**: judge the literal statement, "implies/suggests/vague" never a reason, check the amount before the $100 rule | 0.08 [0.02, 0.24] | 0.95 [0.75, 0.99] | 14 of 24 false-alarm critiques still use "implies/suggests" |

Every disagreement was inspected before each revision, with a decision recorded per case:
[v2](dev-disagreements-ungrounded_claim-v2.md) · [v3](dev-disagreements-ungrounded_claim-v3.md) ·
[v4](dev-disagreements-ungrounded_claim-v4.md). **All 75 were "I disagree with the judge"; no label error was found.**

**Why I stopped revising.** Two content revisions, each aimed at causes documented in a disagreement log, moved dev
TPR from 0.04 to 0.08. v4 explicitly banned the judge's main reason for false alarms, and it kept using it. The
limit is gpt-4o-mini's ability to follow the instructions on these traces, not the definition or the labels.

## Frozen judge and test

Frozen: **v3** (best on dev on both rates). Test run once.

| | Value | 95% Wilson interval |
|---|---|---|
| TPR (true pass rate) | **0.08** (2 of 25) | 0.02 – 0.25 |
| TNR (true failure rate) | **1.00** (19 of 19) | 0.83 – 1.00 |
| Scored | 44 of 45 | |

**support-0247** (human Pass) produced no valid verdict on three attempts (`'Not found'` from DocETL); no verdict
was invented. Counted either way, TPR would be 0.08–0.12 and TNR is unchanged. Details and raw output:
[`test-ungrounded_claim-v3.json`](test-ungrounded_claim-v3.json).

Recompute live, from saved predictions, with no model calls:

```
uv run python analysis/run_judges.py
```

## Decision: reject the judge

1. **It flags almost every good reply**: 23 of 25. Of the 42 conversations it flagged on test, 19 (45%) really
   fail — no better than flagging everything in a set where 43% fail. Nobody would act on its flags.
2. **It cannot measure a failure rate.** TPR + TNR − 1 = 0.08; the prevalence correction divides by that, so any
   corrected rate would be dominated by noise.

TNR 1.00 is not a strength here: a judge that fails everything catches every failure for free — the mirror image
of the "always Pass" judge from Lecture 5.

## Candidate disagreement for the video

**support-0005 (dev), human Pass, judge Fail.** The judge wrote that the order details "match the find_order tool
result", then failed the reply because "Your keyboard arrived on April 14th!" *implies certainty about receipt*.
Response: v4 added "judge what a sentence literally states; 'it implies' is never a reason to fail". The judge kept
failing sentences this way, which is the evidence that the limit is the model rather than the prompt.

## Artifacts

| What | Where |
|---|---|
| Labels (Pass = 1) | `analysis/state/hw5_labels/ungrounded_claim.jsonl` |
| Candidate verdicts, recheck changes | `analysis/state/hw5_candidates_ungrounded_claim.jsonl`, `analysis/state/hw5_relabels_ungrounded_claim.jsonl` |
| Inputs, split, exclusions | `analysis/state/hw5_trace_inputs.json`, `analysis/state/splits.json`, `analysis/state/hw5_exclusions.json` |
| Prompts | `analysis/prompts/ungrounded_claim-v0.txt` … `-v4.txt` |
| Judge records (predictions, critiques) | `analysis/state/judges/ungrounded_claim-v*.json` |
| Code | `analysis/run_judges.py`; Judge tab in `analysis/review_app/` |
| Metrics | `analysis/report/dev-ungrounded_claim-v2.json` … `-v4.json`, `analysis/report/test-ungrounded_claim-v3.json` |
