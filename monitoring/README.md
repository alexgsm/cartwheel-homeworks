# Homework 7 monitor: `unsupported_policy_claim`

## Setup

- **Failure mode:** `unsupported_policy_claim`. The agent states a policy fact that no policy it looked up supports.
- **Judge:** the course reference judge `unsupported_policy_claim-v3` on `claude-opus-4-6`, frozen.
  My own Homework 5 judge (`ungrounded_claim`) was rejected on its test set (TPR 0.08), so I could not use it here.
  On the held-out test set, the reference judge has failure sensitivity 0.83 (10 of 12 failures flagged) and
  pass specificity 0.95 (36 of 38 passes left unflagged).
- **Cartwheel model:** `anthropic/claude-haiku-4-5` in both periods.
- **Periods:** the same 50 scenarios in both.
  - before: the Homework 3 run, 2026-09-18 23:50 to 2026-09-19 00:30 UTC (54 traces, 50 conversations).
  - after: a new run, 2026-09-29 00:24 to 00:33 UTC (54 traces, 50 conversations).
- **Sampling:** a random 20% (10 conversations) for the estimate, plus every conversation in the
  `policy_lookup` and `write_action` risk groups for inspection.
- **Threshold:** 0.15, chosen before I looked at any judge result.

## Results

| Period | Random flagged | Raw | Corrected | 95% interval | Above 0.15? |
|---|---|---|---|---|---|
| before | 3 of 10 | 0.30 | 0.32 | 0.00 to 0.76 | yes |
| after | 4 of 10 | 0.40 | 0.44 | 0.06 to 0.93 | yes |

| Period | policy_lookup flagged | write_action flagged |
|---|---|---|
| before | 4 of 10 (0176, 0236, 0237, 0243) | 2 of 4 (0133, 0236) |
| after | 3 of 8 (0135, 0174, 0242) | 1 of 3 (0242) |

Chart: `prevalence.svg`. History: `history.jsonl`.

## 1. Did the corrected failure estimate move between the two periods?

Yes, a little. The corrected estimate went from 0.32 to 0.44. The difference is one conversation: 3 of 10 flagged
before and 4 of 10 after. The agent, prompt and model did not change between the periods.

## 2. Do the intervals support a conclusion, or is the result uncertain?

The result is uncertain. The intervals overlap almost completely (0.00 to 0.76 and 0.06 to 0.93). Any true rate
between about 0.06 and 0.76 fits both periods, so the change could be sampling luck. The intervals are wide
because only 10 random conversations were judged in each period, and because the judge's accuracy was measured on
only 50 labeled conversations. To detect a real change, the random sample needs to be larger (a higher
`random_rate`), which costs more judge calls.

## 3. What did the risk groups reveal that the random estimate did not?

The random sample only gives an uncertain overall rate. The risk groups give specific conversations to read first.
The most important are the write-action conversations, where the agent issued a refund or cancelled an order while
making an unsupported policy claim: 0133 and 0236 before, 0242 after. These are the most expensive mistakes for a
customer. Policy-lookup conversations were flagged at about the same rate as the random sample, so the failure is
not concentrated there. The flagged conversations differ between the periods, so the failure is not tied to a few
fixed scenarios.

## 4. What action should happen if the estimate crosses the threshold?

Both periods are above 0.15, so error analysis should start now:

1. Read the flagged traces, starting with the write-action ones, and confirm each flag by hand. The judge can be wrong.
2. Add each confirmed failure as a new evaluation case in the Homework 6 suite, so CI checks it on every change.
3. Record any new kind of failure found while reading as a new failure mode.
4. Fix the cause, which is likely in the system prompt. Then check that CI passes and that the next monitoring
   period shows a lower rate.

## Notes

- **Repeat run:** running the `after` period a second time updated the existing Langfuse scores by `score_id`.
  It did not create duplicates (44 monitor scores in total). The DocETL cache returned the same verdicts, so the
  second run made no new judge calls.
- **Langfuse change:** this Langfuse version (3.225) rejects a score with no trace, session or dataset run. The
  period-level corrected-prevalence score is therefore attached to the session `hw7-monitor`, with the period's
  end time as its timestamp.
- **Verdict dates:** the verdict scores of both backfilled periods carry the date the monitor first ran
  (2026-09-29), because Langfuse keeps a score's original timestamp when it updates it. Later daily runs date
  each score by its trace.
