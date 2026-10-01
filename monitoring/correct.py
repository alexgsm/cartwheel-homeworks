"""Bias-corrected failure prevalence for a monitoring period."""

from __future__ import annotations

from typing import Any, Sequence


def corrected_mode_prevalence(
    sample_preds: Sequence[int],
    test_labels: Sequence[int],
    test_preds: Sequence[int],
    confidence: float = 0.95,
    bootstrap_iterations: int = 20000,
    seed: int | None = 7,
) -> dict[str, Any]:
    """Bias-corrected live prevalence for one mode from sampled verdicts.

    The contract, precisely:

      1. ``raw`` is the uncorrected flag rate: ``mean(sample_preds)``.
      2. Compute the frozen judge's failure sensitivity and pass specificity
         from ``test_labels`` and ``test_preds``. Both use the monitoring
         convention that 1 means a failure is present. Failure sensitivity is
         the flagged fraction of human-labeled failures. Pass specificity is
         the unflagged fraction of human-labeled passes.
      3. Compute the Rogan-Gladen point estimate, then resample the held-out
         records and sampled predictions to obtain a percentile-bootstrap
         interval. Use a seeded NumPy generator so the committed result is
         reproducible.
      4. Resample the monitoring predictions and the paired held-out records
         independently with replacement. Keep their original sample sizes.
         Discard a draw if the correction cannot be computed. Clamp each
         retained estimate to [0, 1], then take the percentile interval.
         Raise ``ValueError`` if no replicate is valid.

    Args:
        sample_preds: the judge's 0/1 verdicts over the UNIFORM BASE sample
            only (never the risk strata; they are biased toward failure by
            design).
        test_labels: human labels for the frozen Homework 5 judge's test
            split.
        test_preds: the frozen judge's predictions on that test split.
        confidence: interval confidence level.
        bootstrap_iterations: number of percentile-bootstrap replicates.
        seed: numpy seed for a reproducible interval; None leaves the RNG
            untouched.

    Returns:
        {"raw", "corrected", "ci_low", "ci_high", "confidence",
         "failure_sensitivity", "pass_specificity", "n_sample"}
        with "corrected" clamped to [0, 1] and rates rounded to 4 places.

    Raises:
        ValueError: if an input is empty, the held-out inputs have different
            lengths, a value is not 0 or 1, a class is absent, the judge is
            missing a usable correction, or no bootstrap replicate is valid.
    """
    import numpy as np

    sample = [int(p) for p in sample_preds]
    labels = [int(y) for y in test_labels]
    preds = [int(p) for p in test_preds]
    if not sample or not labels:
        raise ValueError("sample and held-out inputs must be nonempty")
    if len(labels) != len(preds):
        raise ValueError("held-out labels and predictions differ in length")
    if any(v not in (0, 1) for v in sample + labels + preds):
        raise ValueError("every value must be 0 or 1")

    def rates(ys: Sequence[int], ps: Sequence[int]) -> tuple[float, float] | None:
        fails = [p for label, p in zip(ys, ps) if label == 1]
        passes = [p for label, p in zip(ys, ps) if label == 0]
        if not fails or not passes:
            return None
        failure_sensitivity = sum(fails) / len(fails)
        pass_specificity = 1 - sum(passes) / len(passes)
        return failure_sensitivity, pass_specificity

    def rogan_gladen(raw: float, sensitivity: float, specificity: float) -> float | None:
        denominator = sensitivity + specificity - 1
        if denominator <= 0:
            return None
        return (raw + specificity - 1) / denominator

    observed = rates(labels, preds)
    if observed is None:
        raise ValueError("the held-out labels need both a failure and a pass")
    sensitivity, specificity = observed
    raw = sum(sample) / len(sample)
    point = rogan_gladen(raw, sensitivity, specificity)
    if point is None:
        raise ValueError("the judge is no better than chance; the correction is undefined")

    rng = np.random.default_rng(seed) if seed is not None else np.random.default_rng()
    sample_arr = np.array(sample)
    labels_arr = np.array(labels)
    preds_arr = np.array(preds)
    estimates = []
    for _ in range(bootstrap_iterations):
        s = sample_arr[rng.integers(0, len(sample_arr), len(sample_arr))]
        idx = rng.integers(0, len(labels_arr), len(labels_arr))
        drawn = rates(labels_arr[idx].tolist(), preds_arr[idx].tolist())
        if drawn is None:
            continue
        value = rogan_gladen(float(s.mean()), *drawn)
        if value is None:
            continue
        estimates.append(min(1.0, max(0.0, value)))
    if not estimates:
        raise ValueError("no bootstrap replicate produced a valid correction")
    tail = (1 - confidence) / 2 * 100
    ci_low, ci_high = np.percentile(estimates, [tail, 100 - tail])

    return {
        "raw": round(raw, 4),
        "corrected": round(min(1.0, max(0.0, point)), 4),
        "ci_low": round(float(ci_low), 4),
        "ci_high": round(float(ci_high), 4),
        "confidence": confidence,
        "failure_sensitivity": round(sensitivity, 4),
        "pass_specificity": round(specificity, 4),
        "n_sample": len(sample),
    }
