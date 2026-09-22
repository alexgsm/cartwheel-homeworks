# Interface comparison

Review app: `analysis/review_app/` (reference: `analysis/server.py`, `analysis/ui/index.html`).

## One design I retained from the reference interface

Free-text notes written in a margin next to the content being judged. In plain Langfuse I had nowhere to
write notes, so I would have kept them in a separate file and lost track of which trace was which. In the
app the note sits next to the answer, which made reviewing 100 traces practical. I also kept the reference
idea of showing AI suggestions separately from my own notes, with explicit Accept and Reject buttons
(18 accepted, 7 rejected in the depth-search batch).

## One design I changed after inspecting my traces

I added a **scenario notes** line to every conversation header: the tuple, the expected outcome and the
hidden facts from the Homework 3 scenario (for example the matching product ids, the delivery date, or
the order that really belongs to the user). Langfuse does not show these facts, and the traces alone often
look fine. In trace 0144 the agent told a merchant there were no home and kitchen items under $366, but the
scenario notes listed 25 matching products. Without them I would have assumed there really were no matches
and marked the trace as fine.

## One limitation remaining in my interface

The app shows orders the way the tools return them: with a product id but no product name. In trace 0021
the agent called two orders "Slim Pencil Set orders", and I could not tell from the screen that one of them
was actually a Slim Dinner Plate Set without an additional lookup in the product catalog. Joining product
titles into the order view would make identification failures visible at a glance.
