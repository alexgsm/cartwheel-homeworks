# Raindrop Workshop notes (HW4 Part C)

## Setup

- Workshop installed locally on Windows with the official installer (`curl -fsSL https://raindrop.sh/install | bash`
  from Git Bash) and started with `raindrop workshop` on `http://localhost:5899`. On Windows the first start
  failed: the daemon unpacks its UI with `tar`, and Git Bash's GNU tar reads `C:\...` as a remote host.
  Running it with Windows' own tar first on the PATH (`PATH="/c/Windows/System32:$PATH" raindrop.exe workshop`)
  fixed it.
- Instrumented with the `/instrument-agent` skill using the Raindrop Python SDK (`raindrop-ai` 0.0.69) at the
  single turn entry point, `server/app.py` → `post_message`: `begin(...)` before the run (input, session id,
  role, scenario id, prompt version, store id), `track_tool(...)` for every tool call with its arguments and
  result, `finish(output=...)` after. Code in `observability/instrument.py` (`setup_workshop`,
  `workshop_tool_calls`); active only when `RAINDROP_LOCAL_DEBUGGER` is set, so normal runs and tests are unchanged
  (full test suite: same result with and without the change).
- **Existing OpenTelemetry and Langfuse instrumentation preserved.** Langfuse still owns the tracer provider
  (verified: same provider object, `LangfuseSpanProcessor` still registered). Raindrop attaches its span
  processors to that provider rather than installing a second one, so Workshop also receives OpenLLMetry's
  agent, model and tool spans. No existing package versions changed (46 packages added, none changed or removed).
- **Why tool calls first appeared empty** (the problem reported on Discord): the SDK silently drops tool spans
  unless tracing is enabled *with an API key*. Without one, Workshop showed the run but no tool content. Fix
  without sending anything to Raindrop's cloud: point the SDK's endpoint at the local Workshop with a
  placeholder key (`endpoint=http://localhost:5899/v1/`). Remaining quirk: each tool appears twice in the span
  tree — OpenLLMetry's span (no content, because content capture is off for that path) and the SDK span with
  full arguments and results.

## Inspected runs

Eight fresh runs of Homework 3 scenarios, covering all three roles and seven tools, served by the instrumented
server on port 8011 (`scenarios/results/workshop-runs.jsonl`). 0236 is a two-turn conversation, so it has two
Workshop runs.

| Workshop run id | Scenario | Role | Tools called |
|---|---|---|---|
| `4c8946a237e286b54ccbedbe56adc7ce` | support-0130 | merchant | get_order, issue_refund |
| `ae6719d3d07bdc724a7917d4ca60c5cf` | support-0092 | shopper | search_products ×3 |
| `086d1c8a0adbd643653b286f46444dcd` | support-0141 | merchant | search_products |
| `30ad949992a5fe81def89eabb6ca0f31` | support-0166 | support | get_order, escalate_to_human |
| `efbf0cedfa625b53459118a9653ccf57` | support-0171 | support | search_help_center, get_policy |
| `cdc38018ada0e947a6db7847dc455374` | support-0188 | shopper | find_order, search_help_center |
| `e79bcd9954aa4b6d796f8bd672d21e2f` | support-0217 | merchant | none |
| `26bc08c7f9c8a7c2fa94948480563afc` | support-0236, turn 1 | shopper | find_order, get_order, search_help_center |
| `d07acaccb24dea1d3e299ddb4ca035ba` | support-0236, turn 2 | shopper | find_order, get_order, search_help_center |

## Candidate failures from the coding agent, and my decisions

These were hypotheses from the coding agent's reading of the Workshop traces, not labels.

| # | Run | Candidate failure identified by the coding agent | Maps to | Decision |
|---|---|---|---|---|
| S1 | 0130 | `issue_refund($401)` called right after `get_order` returned `refund_eligible: false`; reply then offers a "goodwill exception" through escalation | `unsafe_write_attempt`, `escalation_with_false_exception` | **Accepted** |
| S2 | 0141 | Passed store `"12"` (an id) where the tool expects a store name, got `not_found`; told the merchant "I'm getting an error with the store ID" and asked for the store name the merchant had just given | `parameter_error`, `exposes_internals`, `ignores_user_provided_detail` | **Accepted** |
| S3 | 0217 | No tool call at all, then "I can help you cancel order 990006" for an order that does not exist | `unverified_order_identification` | **Accepted** |
| S4 | 0188 | "Sellers can set stricter rules on top" (the store-overrides policy says stricter or looser); presents two orders as "portable jam trio orders" when one is a different product | `ungrounded_claim`, `unverified_order_identification` | **Accepted** |
| S5 | 0171 | Generic security tips presented under "Based on our policy (cw-account-security)" | `ungrounded_claim` | **Accepted** |
| S6 | 0236 | Both turns offer escalation for an "exception" on clear past-window denials; turn 1 quotes `store-cascade-audio-policy` to a shopper | `escalation_with_false_exception`, `exposes_internals` | **Accepted** |
| S7 | 0092 | After three empty searches, suggests product types (bikes, grills, outdoor furniture) the catalog may not carry — proposed as a new mode, "invents catalog scope" | new mode | **Rejected** — suggesting product types is a reasonable prompt for the shopper to narrow the search, not a claim about what the catalog contains, and the reply correctly says it found nothing "with those searches". No new mode added. |
| S8 | 0166 | Dispute escalated with a ticket, no refund attempt and no exception promise | close negative for `escalation_with_false_exception`, `unsafe_write_attempt` | **Accepted** as a pass |

## Case with uncertainty or an alternative explanation

**0236, turn 1:** "the return window closed on May 10, 2026. Since we're now past that date, the item is no
longer eligible." The model is never given the current date, so "we're now past that date" could be an
unsupported elapsed-time claim (`wrong_time_arithmetic`). **Alternative explanation:** the agent may have
inferred "past" from `refund_eligible: false`, which the tool did return, making the statement grounded. The
trace cannot distinguish the two; RESP-6 (date in the session context) would remove the ambiguity.

## What Workshop added

- **Failures are probabilistic.** Re-running reviewed scenarios changed behaviour: 0166 and 0236 turn 2 did not
  attempt the refund probe they showed in the reviewed traces, and 0092 described its empty search honestly
  ("with those searches") instead of claiming the catalog has none. The same failures recur (S1, S2, S3, S4
  match the reviewed traces), but not on every run, so Homework 5 should measure rates over many traces rather
  than treating one run per scenario as the answer.
- **No new failure mode.** Every accepted suggestion maps to an existing mode, which agrees with the
  saturation result from the final random batch. The one proposed new mode (S7) was rejected.
- Compared with Langfuse, Workshop's value here was the side-by-side tool arguments and results in one tree;
  it surfaced nothing the Langfuse traces did not already contain.
