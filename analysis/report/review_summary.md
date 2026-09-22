# Review summary

## Reviewed sample

**100 distinct conversations** (107 Langfuse traces; 7 conversations have two turns, grouped by
`cartwheel.session_id`), drawn from the 250 Homework 3 support scenarios. No conversation counts toward more
than one batch. Batch definitions and every item's selection reason are in `analysis/state/sample_manifest.json`.

| Batch | Selection | Traces | With a failure note | Marked fine |
|---|---|---|---|---|
| 1 | 15 uniform random + 15 cluster representatives (k-means on turns, tool calls, tool errors, write calls, reply length, role) | 30 | 21 | 9 |
| 2 | One dimension chosen before looking at outcomes: user role, 10 shopper / 10 merchant / 10 support, random within role | 30 | 23 | 7 |
| 3 | Depth searches: rejected write-tool calls (8), `find_order` with more than one match and no `get_order` (9), internal identifiers in replies to shoppers (8) | 25 | 23 | 2 |
| 4 | 15 uniform random after drafting the taxonomy | 15 | 9 | 6 |

Composition of the 100: 64 shopper, 21 merchant, 15 support; 72 coverage and 28 challenge scenarios
(13 data-quality cases, 4 members of authorization pairs); intents refund 38, order status 24, product
search 12, policy question 10, cancellation 9, return eligibility 3, dispute 2, out of scope 2.

Depth-search suggestions were retrieval signals, not labels: **18 accepted, 7 rejected**
(`analysis/state/suggestions.json`).

## Taxonomy and sample fractions

Eight final modes (full definitions, boundaries, positives, close negatives, evaluator type and requirement
source in `analysis/state/patterns.json`). Every reviewed trace received a present or absent judgment for
every final mode (800 judgments), written to Langfuse as scores and to `analysis/state/labels/<mode>.jsonl`.

These are **sample fractions, not prevalence estimates**: batch 3 deliberately searched for failures, so
modes it targeted (especially `unsafe_write_attempt` and `exposes_internals`) are over-represented.

| Mode | Present | Sample fraction | Evaluator | Requirement |
|---|---|---|---|---|
| `ungrounded_claim` | 27 | 0.27 | LLM judge | RESP-3, RESP-2 |
| `exposes_internals` | 21 | 0.21 | code check | RESP-1 (revised) |
| `escalation_with_false_exception` | 20 | 0.20 | LLM judge | RESP-7 (new) |
| `unverified_order_identification` | 20 | 0.20 | LLM judge | RESP-3 |
| `ignores_user_provided_detail` | 13 | 0.13 | LLM judge | RESP-9 (new) |
| `unsafe_write_attempt` | 11 | 0.11 | code check | RESP-8 (new) |
| `mishandles_broken_record` | 8 | 0.08 | LLM judge | RESP-3, ESC-3 |
| `parameter_error` | 3 | 0.03 | code check | TOOL-2, TOOL-3, TOOL-7 contracts |

67 of 100 traces have at least one final mode.

Five further modes were observed and documented in `patterns.json` but not labelled as final, to stay
within the 5–8 limit: `search_result_overstated` (6 of 100; labelled before it was moved, see the AgentDebug
section), `unrequested_detail` (shopper-facing field dumps), `wrong_time_arithmetic`,
`wrong_approval_expectation` (refunds above $100 presented as immediate), and `order_existence_leak`
(authorization pairs 0206/0207, 0208/0209, 0216/0217 reply differently for "not yours" and "does not exist").

## Stability: the final 15

New failure types first seen per batch: **10, 4, 0, 0**. In the final 15 random traces, 9 had at least one
failure and **0 produced a previously unseen consequential mode**, so the taxonomy is treated as stable.
(`parameter_error`, added later from the AgentDebug comparison, has one positive in the final 15 — 0141 —
but the same behaviour already appeared in batch 1 as 0090's mistyped policy id; it was unnamed, not unseen.)

## One taxonomy revision

**`empty_result_reported_as_fact` → `search_result_overstated`.** In batch 1 the mode covered one
behaviour: the agent searched with a category word, got no results, and told the user there were no
matches (0092: "no outdoor products under $260" when 25 existed; 0101, 0144 the same). In batch 2, trace
0143 did not fit: the search did return products, but the agent showed 5 of the 40 matching toys and called
them "the toys under $340 in your inventory". My first instinct was to name the shared cause (a category
word used as a product search), but a judge cannot see causes, and a truncated list from `limit: 5` misleads
the user in the same way. What both traces share from the user's side is that a search result is presented
as the complete answer. The mode was widened and renamed to cover both the empty and the truncated case.
The boundary is honesty about the search: "I searched for 'beauty' and found nothing — want me to try a
product name?" is a pass. (Later, when `parameter_error` was added, this mode was moved from final to
documented to keep the taxonomy at eight; its labels are kept.)

## Specification revisions

Mapping each mode to `SPEC.md` showed that four had no requirement behind them, and one contradicted the
spec. I decided what the agent should do in each case and revised `SPEC.md` (section 6, "Revisions from
HW4 error analysis"):

- **RESP-1 revised — citations by audience.** RESP-1 required citing the policy identifier, but 21
  reviewed replies that did so to shoppers read as internal jargon ("per cw-returns"). Decision: support
  staff get the identifier; shoppers and merchants get the policy's public title. Motivating annotations:
  0079, 0081, 0083, 0090, 0232. Close negatives: 0171, 0199 (support staff).
- **RESP-6 added — dates and elapsed time.** The model is never given the current date, so it guesses:
  0120 told a merchant an order was "delivered about 4 days ago" when it was delivered 507 days earlier
  (the gap between order and delivery dates). Decision: the server supplies the date in the session
  context, and any elapsed-time statement must be computed from it and the right record date. Giving the
  date alone is not enough — 0120 subtracted the wrong two dates — so the requirement also names which
  dates to use, which makes it a code check. Also 0152, 0221, 0223.
- **RESP-7 added — decide, do not defer.** When policy and the order record decide a request, state the
  decision and the rule; do not escalate and do not suggest a human may grant an exception. Motivating
  annotations: 0036, 0046, 0038, 0231, 0244 (escalated after the user pushed), 0059.
- **RESP-8 added — write tools only for confirmed requests.** Never call `issue_refund` or
  `cancel_order` to check eligibility. Motivating annotations: 0035 (refund called with amount 0 and
  reason "checking eligibility"), 0130, 0043, 0127, 0219 (refund attempted after the read tools reported
  ineligible), 0133 (cancellation executed on a "how do I" question).
- **RESP-9 added — use what the user gave.** Use a named store, product, price or order in tool calls
  before asking, and never re-ask for it. Motivating annotations: 0202, 0192, 0220, 0182, 0087.

The specification is not read at runtime; these revisions still need to be carried into the system prompt,
session context and tool code before they change agent behaviour.

## Comparison with the AgentDebug taxonomy

AgentDebug's AgentErrorTaxonomy groups agent errors into memory, reflection, planning, action and
system modules. Most of my modes have a counterpart: `ungrounded_claim` ~ Hallucination;
`search_result_overstated` and 0178's ignored store mismatch ~ Outcome Misinterpretation;
`ignores_user_provided_detail` ~ Retrieval Failure; the store-window misses (0221, 0223) ~ Constraint
Ignorance. Format Error, Step Limit Exhaustion, LLM Limit and Environment Error did not occur in my traces.

Two AgentDebug types had no counterpart: **Inefficient Planning** (my dropped `wasteful_tool_calls`,
0163 and 0090) and **Parameter Error**. I chose Parameter Error as the omission because it names a
failure my notes had described only by its consequences. A search of all 250 conversations for tool calls
rejected for a bad argument found six: 0035 (refund amount 0), 0090 (policy id `cw-restocking-fee`
instead of `cw-restocking-fees`), 0141, 0142, 0195 and 0200 (a store **id** passed where `search_products`
expects a store **name**). Three are in my reviewed sample, which meets the three-positive minimum, and
the store-id error recurring four times shows it is systematic rather than a one-off. I added
`parameter_error` as a final mode (code check) and moved `search_result_overstated`, the smallest mode, to
documented to stay at eight. Its fix differs from the others: the session context gives merchants a store
id, so the tools should accept an id (or the context should include the store name), and policy ids should
come from search results rather than being retyped.

AgentDebug's names describe the agent's internal module (memory, planning); mine describe what the user
experiences. "Hallucination" is less precise for review than `ungrounded_claim`, because a judge can see
that a reply contains an unsupported claim but cannot see whether the agent "remembered" wrongly.

## Product findings (not agent mistakes)

- The model is never given the current date, so every elapsed-time statement is a guess (0120, 0152, 0221, 0223).
- Order tools never return product titles; the agent can confirm the store (`get_order`) but never the item
  (0021, 0026, 0072, 0108, 0109, 0110).
- The session gives merchants a store id, but tools that filter by store expect a store name (0141, 0202).
- Product search has no category filter, so category requests fail or succeed by accident (0092, 0095, 0101,
  0143, 0144).
- There is no behaviour for inconsistent records: across five data-quality flavours the agent never flagged
  one correctly for a customer (0177, 0179, 0180, 0182, 0185, 0196, 0197, 0205).
- Escalation follows the user's frustration rather than the rules: clear past-window denials are escalated
  with talk of exceptions (0036, 0244), while a real non-delivery dispute is not escalated (0108).

## Scenario defects found during review

4 of 100 reviewed scenarios could not be judged as their expected outcome describes: 0103 and 0105 are
labelled out of scope but ask for in-scope order help; 0239's follow-up refers to an order the first message
never mentioned; 0088 expects a store-specific policy but the message names no store.
