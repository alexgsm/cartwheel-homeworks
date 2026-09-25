# Homework 5, Part A — failure mode and definition

## Mode

`ungrounded_claim` (from Homework 4). Chosen because it has the most failures in the reviewed sample
(27 of 100 conversations) and cannot be checked with code: deciding whether a sentence in a reply is supported
by a tool result needs reading, not a pattern match.

## The question the judge answers

> **Every fact, policy, capability or outcome in the agent's replies is backed up by a tool result or by words
> the user wrote in the trace, except (1) promises that a human may grant an exception, (2) claims about which
> order the user means, and (3) offers to look something up next in this chat.**

True → **Pass**. False → **Fail**. The unit is the **whole conversation**: in a two-turn conversation, an
unsupported claim in either reply is a Fail.

## Evidence the judge may use

Only what is in the trace: the user's messages, the tool calls and **their results**, and the reply. A tool
being *called* is not evidence; only what it *returned* counts (0090 called `get_policy` and got `not_found`).
General knowledge of e-commerce, and whether a claim happens to be true in the real world, do not count.

## Fail rules

The reply states something that neither a tool result nor the user's own words support. Includes:

- invented policy or fee (0090: "the store provides a shipping label")
- invented capability or outcome (0070, 0067: "your refund will arrive shortly" after a cancel that returned
  only `{ok, cancelled}`)
- a refund above $100 described as immediate (0055, 0238) — `cw-refunds` sends those to human review
- a wrong elapsed time (0221, 0223)
- a detail the user never gave (0023: "unopened")

## Pass rules

- Every claim traces to a tool result or to the user's message (0079: the $100 rule comes from `cw-refunds`).
- Repeating what the user said, before any tool call ("your lamp arrived broken").
- Clumsy wording of a supported fact.
- The reply's only unsupported statement is one of the three exclusions in the sentence.
- An offer to look something up next in this chat ("tell me the order number and I'll look it up"), even
  when no tool result shows the lookup would return what is offered (candidates 0201, 0096).

## The agent's own tool definitions count as support (added 2026-09-25, during labelling)

The agent sees one-line descriptions of its tools, and a rule stated there is not invented when the agent
repeats it. Example: `cancel_order` is described as "Cancel an order that has not shipped yet", so "an order
that has shipped can't be cancelled" passes without a policy lookup (candidate 0080; matches the Homework 4
label for 0072). The judge cannot see these descriptions in the trace, so the judge prompt must include them.
This is the agent's configuration, not a label, so it is not leakage.

A **reason** the agent supplies for an outcome still needs support. Candidate 0080 said the other orders were
"not eligible for refunds based on their age or store policies"; the tool result gives only
`refund_eligible: false`, no tool description gives a reason, and the system prompt tells the agent to call
`issue_refund` for the specific reason. The guessed reason is an invented fact → **Fail**, even though it is
plausible.

## Offers vs claims about what will happen (added 2026-09-25, during labelling)

Decided on the first three candidates: an offer to **look something up** next in the conversation is not an
ungrounded claim. A statement about **how things work or what will happen** still is, even in the future tense
or phrased as an offer:

| Excused (Pass) | Still counts (Fail if unsupported) |
|---|---|
| "tell me the order number and I'll look it up" | "you should see the refund processed shortly" (0070) |
| "I can pull up the full details for you" | "I can process the refund right now" for $257 (0238; also 0054, 0055) |

Checked against Homework 4: every Fail whose note mentions an offer also has an unsupported claim of the
second kind, so no Homework 4 label changes. The narrow wording ("look something up", not "what the agent will
do next") was chosen because the broad one would have excused 0238, 0054 and 0055, which the Homework 4
boundary explicitly keeps in this mode (refunds above $100 presented as immediate).

## Boundaries with neighbouring modes (decided in Homework 4, kept here)

| Excluded claim | Belongs to | Example |
|---|---|---|
| A human "may be able to make an exception" | `escalation_with_false_exception` | 0036 |
| Which order the user means, asserted without checking | `unverified_order_identification` | 0072 |

The exclusion covers only that statement. Any *other* unsupported claim in the same reply still fails
(0046: exception promise excused, "unworn condition will help your case" is not).

Checked against the Homework 4 labels before adopting it: of 23 conversations with an exception promise, 14
are labelled Pass here (promise was the only issue); of 20 with an order-identity problem, 12 are Pass. Every
overlapping Fail has a separate invented claim, except **0239**, whose note cites only the exception promise —
to be rechecked.

## Considered and rejected

Counting order guesses here as well (would have changed 12 labels and overlapped with another judge).

## Two more rules, settled during the recheck (2026-09-25)

- **A hedge does not make a guess supported.** "It *may* be outside the return window", "they *may* all be
  priced higher", "you *may* be eligible for a return instead" are unsupported claims like any other. This is
  what the Homework 4 labels already did (0141, 0150, 0039 are Fail for hedged guesses).
- **Exclusion (1) covers only the promise that an exception might be granted.** Anything the reply adds about
  the exception or the escalation — when an exception applies ("like if the item is defective"), what the human
  will do ("ask for photos"), what they can offer ("a replacement") — still needs support. Decided on 0046
  ("unworn condition will help your case") and applied consistently.

## Recheck of the Homework 4 labels against this definition

The definition was sharpened while labelling the enrich candidates (tool definitions, offers, reasons, hedges,
exception details), so every Homework 4 label kept in the dataset was rechecked, as the handout requires
("If you change your failure definition, recheck the affected labels before testing"). Method: three
independent agent reviewers read all 89 kept Homework 4 conversations against this file and flagged possible
conflicts; every flag was verified against the trace, and each change was confirmed by the human.

- **12 Homework 4 labels changed, all Pass → Fail**: 0038, 0044, 0072, 0081, 0095, 0112, 0135, 0151, 0157,
  0163, 0219, 0236. Each change and its reason is in `analysis/state/hw5_relabels_ungrounded_claim.jsonl`;
  the Homework 4 label files are unchanged.
- **3 candidate labels changed, Pass → Fail**: 0237 and 0241 (their second turn had not been shown when they were
  labelled — see below) and 0073 (its precedent, 0072, became Fail).
- **0239 stays Fail.** Its note cited only the exception promise, but the reply also says an exception may apply
  "like a defect", which the exception-detail rule counts.
- Flags examined and **not** changed: 0021 ($96.50 refund "right away" — not a large refund), 0105 (orders offered
  as the ones the user means — exclusion 2), 0110 (delivery date matches the order the agent picked), 0133
  ("reflected in their account" restates a cancellation the tool confirmed), 0170 (the `cancel_order` definition
  backs the capability), 0216 (the store id comes from the session context), 0092 and 0101 (a faithful reading
  of a search that returned nothing).

**Input bug found by the recheck and fixed.** The first version of `prepare_inputs()` kept only the first turn of
each two-turn conversation. Several labels rest on a claim made in the second turn (0108, 0238, 0244), so the judge
would have been scored on text that does not contain what was labelled. Each record now holds the whole
conversation. Three candidates were two-turn conversations whose second turn had not been shown while labelling
(0237, 0240, 0241); their second turns were then reviewed, which changed 0237 and 0241.

**Final labelled set: 112 conversations — 65 Pass, 47 Fail.** Exclusions (11 close scenario variants, all Pass)
are in `analysis/state/hw5_exclusions.json`.
