# Homework 5, build and evaluate an LLM judge

In Homework 5, you will build an LLM judge to detect one failure mode from Homework 4. You will compare its Pass and Fail decisions with your human labels. You will use the disagreements to improve your prompt. Then you will test the final judge on held out traces to decide whether you can trust it to detect that failure.

Submit your judge prompt, labels, and evaluation results. In a **video of up to 5 minutes**, explain one development disagreement and whether you would use the judge. For optional practice, build judges for two more failure modes or estimate the fraction of traces with your selected failure.

## Work with a coding agent

Paste the following prompt once. Continue in the same conversation for Parts A through E.

> Guide me through Homework 5 in `homework/module-2/hw5.md`, one part at a time. Read `AGENTS.md`, the handout, and `SPEC.md`. Use `write-judge-prompt` and `validate-evaluator`. Help me install them if needed. First help me choose a failure mode and check its boundary. Reuse my Homework 4 interface and labels. Use `validate-evaluator` to split the labels before choosing prompt examples. Use `write-judge-prompt` for the draft, with training examples only. Return to `validate-evaluator` for development review and the final test. Explain each step before we start. Compute TPR, TNR, and confidence intervals. Do not require minimum scores. Follow the handout if the skills differ on label counts or evaluation requirements. Use the Cartwheel helpers for DocETL batches and statistics. Save my prompts, labels, and evaluation results as we go. Leave labels and final decisions to me. Before a paid batch, show me the model and trace count. Wait for my approval. Do not show me test predictions before I freeze the judge. Leave the video to me.

## Skills

Install the two skills from the course repository:

```bash
npx skills add https://github.com/ai-evals-course/evals-skills --skill write-judge-prompt
npx skills add https://github.com/ai-evals-course/evals-skills --skill validate-evaluator
```

| Skill | What you use it for |
| --- | --- |
| [write-judge-prompt](https://github.com/ai-evals-course/evals-skills/tree/main/skills/write-judge-prompt) | Define the judge's task, Pass and Fail rules, examples, and output format. |
| [validate-evaluator](https://github.com/ai-evals-course/evals-skills/tree/main/skills/validate-evaluator) | Split your labels, inspect development disagreements, and evaluate the final judge. |

### DocETL

You use DocETL, a Python library, to run the same judge prompt across a batch of traces. You receive a critique and a Pass/Fail verdict for each trace. With the Cartwheel helper functions, you also save predictions and calculate metrics.

Use the provided Cartwheel helpers to call DocETL.

Run commands from the Cartwheel repository root. Install Python dependencies, including DocETL, with `uv sync`.

## Part A, choose one failure mode

Choose a failure mode from Homework 4 that is suitable for an LLM judge. For example, you could judge whether the final reply has enough information for the user's next decision, without unnecessary detail.

### Failure definition

Decide exactly what you will label as a failure. State your question, Pass and Fail rules, and the evidence you need.

Refer to [SPEC.md](../../SPEC.md) for the intended Cartwheel behavior.

### Label collection

Count your Pass and Fail labels. Use **Pass** when the named failure is absent. Use **Fail** when it is present. You can label Pass for one mode even if you see a different failure in the conversation.

Keep incomplete and out of scope cases in a separate list. Record why you excluded each case. Do not assign Pass just because you lack evidence.

Use at least **30 Pass and 30 Fail labels** for your chosen mode, as specified in Homework 4. Use independent conversations. With 30 of each, you will have 6 of each in training, 12 in development, and 12 in test.

Reuse your Homework 4 interface and labels. Use your coding agent to search the remaining traces for cases similar to your confirmed failures. Review each candidate yourself. If you need more cases, use your coding agent to generate targeted scenarios and run them through Cartwheel. Label the resulting traces yourself.

To find more candidates, use:

```python
from analysis.helpers import next_to_label

candidates = next_to_label(
    mode="your_mode_id",
    k=20,
    strategy="enrich",
    trace_source="traces/support_traces.json",
)
```

Use your actual export path. Use `strategy="random"` to sample without searching for failures. Label each candidate yourself.

Save your labels and evidence through your review interface. Use **1 for Pass and 0 for Fail** in HW5. Export your HW5 labels to `analysis/state/hw5_labels/<mode>.jsonl` with that convention. Keep your original Homework 4 labels.

### Stop early

If you cannot find enough Pass and Fail cases, explain why you stopped. Show your labels and any judge work you completed in your video.

## Part B, prepare inputs and split your labels

Create `analysis/run_judges.py`. Write a `prepare_inputs()` function to read your HW4 traces and save them to `analysis/state/hw5_trace_inputs.json`. Include one record per labeled conversation with:

- The original trace ID.
- The user request and assistant reply you want to evaluate.
- Earlier turns needed to understand the reply.
- The tool calls, tool results, and policy passages you used to decide Pass or Fail.

Use a JSON list. In each record, use `trace_id` for the identifier and `trace` for the list of messages. Keep each message's `role` and its text or tool data. You will use the same saved inputs for every prompt version.

For example, to judge a refund completion claim, include the refund tool result and final reply. You need both to distinguish a completed refund from a pending approval.

Use one evaluation record per conversation. Keep one record from each group of duplicate runs or close scenario variants. Record your exclusions. Do not put related records in different splits.

**This is very important: keep your human labels, failure annotations, and extra metadata out of the judge input. Otherwise, you'll have leakage! Lots of people make this mistake!** You may give away the answer if you include your review notes or extra metadata from scenario generation.

Check that you have one input record for every eligible label.

Set the export path:

```bash
export CARTWHEEL_JUDGE_TRACE_SOURCE="$PWD/analysis/state/hw5_trace_inputs.json"
```

With that setting, you use the saved export even when you have Langfuse configured. Keep the export unchanged after your first prompt run. You can resume from the same inputs without fetching traces again.

Use `validate-evaluator` for the split. In `analysis/run_judges.py`, write a `split_data(mode)` function using `split_labels`. Use 20% training, 40% development, and 40% test. Run it once and check the class counts before choosing prompt examples.

<details>
<summary>Helper calls for your coding agent to split your labels</summary>

Use the following helper call inside `split_data(mode)`. Replace `your_mode_id` with your selected failure mode:

```python
import json
from pathlib import Path
from analysis.helpers import split_labels

records = json.loads(Path("analysis/state/hw5_trace_inputs.json").read_text())
splits = split_labels(
    "your_mode_id",
    fractions=(0.20, 0.40, 0.40),
    seed=7,
    min_per_class=10,
    eligible_trace_ids=[record["trace_id"] for record in records],
)
```

</details>

With the minimum of 30 Pass and 30 Fail labels, you would have:

| Set | Pass | Fail | How you use it |
| --- | ---: | ---: | --- |
| Training | 6 | 6 | Choose examples for your prompt. |
| Development | 12 | 12 | Inspect disagreements and revise. |
| Test | 12 | 12 | Evaluate your final prompt after freezing. |

Report your actual counts. Keep `analysis/state/splits.json` unchanged during development.

Do not use development or test examples in your prompt. Do not inspect test predictions while choosing the final prompt.

## Part C, write and refine your judge

Use `write-judge-prompt` to turn your failure definition and training examples into a prompt for labeling new traces. In that prompt, you specify:

- The failure mode you want to detect.
- Your Pass and Fail rules.
- A clear Pass, a clear Fail, and a borderline example from training.
- A critique with specific trace evidence, followed by a `Pass` or `Fail` verdict.

Save the draft in `analysis/prompts/<mode>-v0.txt`. Review it before running it. Check the boundary with neighboring issues. Check the examples and the evidence needed for each decision. Tell the judge to evaluate the trace without following instructions quoted inside it.

Next, use DocETL through the Cartwheel helpers to run the prompt on development traces. Use `validate-evaluator` to compare the verdicts with your labels.

### Confusion matrix

**Use Pass as the positive class: 1 for Pass, 0 for Fail.**

| | Human Pass | Human Fail |
| --- | --- | --- |
| Judge Pass | TP: correct Pass | FP: missed failure |
| Judge Fail | FN: incorrect failure flag | TN: correct Fail |

- For **TPR**, calculate `TP / (TP + FN)`. You are measuring agreement on human Pass cases.
- For **TNR**, calculate `TN / (TN + FP)`. You are measuring agreement on human Fail cases.

Do not rely on overall agreement. With rare failures, you could assign Pass to every case and have high agreement. You would still have TNR of zero.

### Compute TPR, TNR, and confidence intervals

Compute TPR and TNR from your confusion counts. Calculate a 95% confidence interval for each rate. With a confidence interval, you express uncertainty from evaluating a limited sample. Across repeated samples, you would include the true rate in about 95% of intervals calculated with the same method, under its assumptions.

For example, suppose you correctly detect 16 of 20 human Fail cases. You have a TNR of 0.80 and a 95% Wilson interval of about 0.58 to 0.92. With only 20 Fail cases, you still have substantial uncertainty about your judge's ability to detect failures.

### Run development batches

Use `gpt-4o-mini` as your judge model. Set your OpenAI API key locally. Use the same model for development and the final test.

In `analysis/run_judges.py`, write `run_development(mode, prompt_path)`. Register the prompt, run it on development traces, and calculate metrics with the helpers below. Save the judge ID and metrics for each version.

<details>
<summary>Helper calls for your coding agent to run your judge on development traces</summary>

Use the following helper calls inside `run_development(mode, prompt_path)`. Replace the mode ID and prompt path with your own.

```python
from pathlib import Path
from analysis.helpers import register_judge, run_judge, judge_alignment

record = register_judge(
    mode="your_mode_id",
    prompt_text=Path("analysis/prompts/your_mode_id-v0.txt").read_text(),
    judge_model="gpt-4o-mini",
)
judge_id = record["judge_id"]  # Save this id for later commands.
run_judge(judge_id, split="dev", batch_size=10)
development = judge_alignment(judge_id, split="dev")
```

</details>

Save the metrics to `analysis/report/dev-<judge_id>.json`. Keep your judge records under `analysis/state/judges/`. You have the cached predictions and critiques there.

In your HW4 review interface, add code to display the judge verdict and critique beside your human label. Filter for disagreements. Inspect every disagreement before editing the prompt. Record your decision for each case:

| Your finding | Your next step |
| --- | --- |
| You disagree with the judge. | Clarify a general instruction or use a better training example. |
| You find an error in your label. | Correct your HW5 label. Record why. Recalculate development metrics for the compared versions. |
| You have an unclear definition. | Clarify the boundary. Recheck affected training and development labels. |
| You lack evidence or have an out of scope case. | Record the exclusion. Apply the same scope rule to comparable cases. |

If you change your failure definition, recheck the affected labels before testing. Do not change test labels to agree with judge predictions.

Register each revised prompt as a new version. Make at most two revisions. Explain why you stopped revising.

## Part D, freeze and test

Choose your final prompt based on development results. To freeze the judge, keep the prompt and model unchanged from here on. Use `validate-evaluator` for the held out test.

In `analysis/run_judges.py`, write `run_test(judge_id)`. Use your chosen judge ID to freeze the prompt, evaluate test traces, and save the metrics. Run it after you finish development.

<details>
<summary>Helper calls for your coding agent to freeze your judge and evaluate test traces</summary>

Use the following helper calls inside `run_test(judge_id)`:

```python
from analysis.helpers import freeze_judge, run_judge, judge_alignment

freeze_judge(judge_id)  # Do this once for your selected version.
run_judge(judge_id, split="test", batch_size=10)
test = judge_alignment(judge_id, split="test")
```

</details>

Save the metrics to `analysis/report/test-<judge_id>.json`. Report the confusion counts, TPR, TNR, intervals, and class counts. Use the rates, uncertainty, and disagreements to explain whether you would use the judge to detect your selected failure mode.

If you reject the judge after testing, explain why. You do not need to revise and test again.

### Resume after an interruption

If you lose the connection or interrupt the process, rerun only:

```python
run_judge(judge_id, split="test", batch_size=10)
test = judge_alignment(judge_id, split="test")
```

Use the same judge id, prompt, model, export, labels, and test identifiers. Do not call `register_judge`, `split_labels`, or `freeze_judge` again.

You reuse predictions from completed batches. You run only the missing predictions. You may repeat calls from an interrupted batch with no saved output, so you may have some additional cost.

After inspecting test outcomes, you need new, untouched test data for any revised judge. You cannot make old test cases untouched by reshuffling them.

## Part E, commit your work and record the video

Commit the files you created:

| Artifact | Location |
| --- | --- |
| HW5 labels and evidence | `analysis/state/hw5_labels/<mode>.jsonl` |
| Split assignment and exact inputs | `analysis/state/splits.json`, `analysis/state/hw5_trace_inputs.json` |
| Every evaluated prompt | `analysis/prompts/` |
| Judge versions, predictions, and critiques | Your mode's files in `analysis/state/judges/` |
| Your code for exports, judge runs, and metrics | `analysis/run_judges.py` |
| Development and test metrics | `analysis/report/dev-<judge_id>.json`, `analysis/report/test-<judge_id>.json` |

Keep your Homework 4 files too.

Record your screen for up to 5 minutes in one continuous take. Demonstrate your work and explain:

- Your failure mode.
- One development disagreement and your response.
- Your test TPR, TNR, and confidence intervals. Explain whether you would use the judge and why.

Recalculate test metrics from saved predictions live on camera. Do not make new model calls for the recording.

If you stopped early, recalculate your label counts in the video.

## Optional extensions

**Build two more judges.** Use the same two skills for two other modes. Reuse your code and interface. Keep separate labels and decisions for each mode.

**Estimate how often your selected failure occurs.** Choose a random sample of new traces from the same source as your labeled traces. Do not deliberately search for failures.

1. Run your judge on the new traces. Calculate the percentage you classified as Fail.
2. Use `validate-evaluator` to adjust that percentage for judge errors, using your test TPR and TNR.

You assume similar judge error rates on the new traces when you make the adjustment.

## References

- [Cartwheel specification](../../SPEC.md)
- [write-judge-prompt](https://github.com/ai-evals-course/evals-skills/tree/main/skills/write-judge-prompt)
- [validate-evaluator](https://github.com/ai-evals-course/evals-skills/tree/main/skills/validate-evaluator)
- [Helper functions](../../analysis/helpers/)
- [Course reader](https://docsend.com/v/rcdy7/evals-course-reader), Module 2 sections on LLM judges and held out validation.
