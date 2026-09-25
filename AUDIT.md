# Evidence audit of the seven shipped NorthLedger sessions

**First run:** 22 September 2026 · **Revised:** 22 September 2026 (see *Revision* below)
**Method:** two automated passes over the recorded transcripts, re-executed against the same
databases (`client_data.db`, and the 74.8M-row `big_data.db`). Scripts: `replay_sessions.py`,
`trace_numbers.py`. Both are re-runnable.

This audit was run *before* publishing, on work that was already finished, to find out whether
the claims hold — not to confirm that they do.

## Revision — what was withdrawn, and why

The first version of Pass 2 reported "95.4% of 954 stated numbers traced to tool output" and
"1 wrong number in 954 (0.10%)", and called 95.4% a floor. An independent review, then my own
re-test, showed all three were unsupported:

- **The matcher was too lenient to mean anything.** It accepted a number within ±0.5%, after
  scaling by 100 or 1,000, or as any substring of the tool output. Run on numbers that are
  **all deliberately wrong**, it still "traced" **66%** of numbers shifted by +3% and **61%** of
  random numbers. 95.4% against a 61–66% null is weak evidence of anything.
- **"A floor" had the direction backwards.** Lenient matching *over*states traceability.
- **"1 in 954" was not a rate.** Only the 44 numbers the lenient matcher could not trace were
  inspected. The other ~900 were never checked for correctness.
- **The total was 931, not 954.** The 954 included a smoke-test session, which is not one of the
  seven shipped sessions.

What stands unchanged: Pass 1 (92/92 queries reproduce), and the one defect below.

---

## Pass 1 — Query reproducibility

Every `run_sql` call recorded in every session was re-executed and its result compared, cell for
cell, to what the transcript recorded at the time.

| Session | Queries checked | Reproduced | Drifted | Errored |
|---|---|---|---|---|
| s1-audit | 19 | 19 | 0 | 0 |
| s2-clean | 0 (python-only) | – | – | – |
| s3-forecast | 0 (python-only) | – | – | – |
| s4-nyc311-ops | 13 | 13 | 0 | 0 |
| s5-rdw-fleet | 26 | 26 | 0 | 0 |
| s6-census-econ | 19 | 19 | 0 | 0 |
| s7-chicago-crime | 15 | 15 | 0 | 0 |
| **Total** | **92** | **92** | **0** | **0** |

What this proves: the queries are real and still return what was recorded.
What it does **not** prove: that a query answered the question it was meant to answer, or that
the prose matched the query. Reproducible is not the same as correct.

---

## Pass 2 — Number provenance (strict matcher)

Every distinct number in the agent's final answers and reports was matched against the numbers
its tools actually returned — **exactly, at the precision the number was printed with**. A tool
share (0.237) matches a printed percentage (23.7) only when a `%` sign sits beside it. No
substring matching. The same matcher is run on two null sets of known-wrong numbers, so the rate
can be read against a baseline.

| Session | Numbers stated | Traced exactly | Untraced | Traced |
|---|---|---|---|---|
| s1-audit | 124 | 71 | 53 | 57.3% |
| s2-clean | 55 | 50 | 5 | 90.9% |
| s3-forecast | 147 | 77 | 70 | 52.4% |
| s4-nyc311-ops | 203 | 179 | 24 | 88.2% |
| s5-rdw-fleet | 157 | 92 | 65 | 58.6% |
| s6-census-econ | 80 | 65 | 15 | 81.2% |
| s7-chicago-crime | 165 | 145 | 20 | 87.9% |
| **Total** | **931** | **679** | **252** | **72.9%** |

| Null set (all numbers wrong) | "Traced" by the same matcher |
|---|---|
| Every stated number shifted by +3% | 10.3% |
| Random numbers of similar magnitude | 8.3% |

**Reading it.** 72.9% of stated numbers appear exactly in what a tool returned, against a
chance rate of 8–10%. The 252 untraced numbers are **not** errors by default: they include
arithmetic the model did (totals, shares), figures rounded to a different precision than the
tool returned, and forecast table cells. They were not individually checked, so **no error rate
is claimed for them.** The session with the most untraced numbers, s3-forecast, is the one whose
numbers are mostly model-computed forecast cells.

---

## The one confirmed defect: a wrong number in a shipped report

`s5-rdw-fleet`, section 5, states:

> Electrified: BEV 892,313 (8.3%), PHEV 1,672,577 (15.5%), **total any-electric 2,564,854**
> (23.7% of cars).

The query behind it returned four groups:

| Group | Count |
|---|---|
| BEV_pure_electric | 892,313 |
| PHEV_Elek_plus_Benzine_or_Diesel | 1,672,577 |
| Elek_plus_other_fuel | 964 |
| No_electricity | 8,236,609 |

- Correct "any electric" total: 892,313 + 1,672,577 + 964 = **2,565,854**
- BEV + PHEV only: **2,564,890**
- **Stated: 2,564,854** — matching neither, and understating the correct figure by 1,000.

The percentage (23.7%) did not expose it: both totals round to the same share, so it survived
every check a reader would apply by eye. Both source counts reproduce exactly — which is why this
class of error is dangerous: nothing around it looks wrong.

A second figure in the same report, "Elektriciteit 2,085,122 (19.3%)", was checked and is
**correct** — it counts cars whose *primary* fuel is electric (`brandstof_volgnummer = '1'`).

This defect was found among the untraced numbers. It is the only error confirmed; it is not a
measured error *rate*.

---

## What this audit changed in the engine

The error was not a hallucination — the model had the right inputs and did the addition inside a
sentence, where nothing could check it. Query discipline was already perfect (92/92). So in the
rebuilt engine:

- `record_fact` and `derive_fact` take **no value**: the engine runs the query, or does the
  arithmetic in `compute_derivation`, and that result is the fact.
- Derived figures record their inputs and are re-added by `EvidenceLedger.verify`; a derived
  figure fails if any input fails.
- Verification now runs **before** the brief is written, and a figure that does not reproduce is
  withheld with both numbers shown.

## Honest limits

- Neither pass can tell whether a query answered the question it was meant to answer. That needs
  ground truth, which is what the defect-injection evaluation provides (`EVALUATION.md`).
- Sessions s2 and s3 did their work in Python, so Pass 1 could not check them.
- A strict exact match can still be a coincidence (a common number like 12 appears in many tool
  outputs); the null baselines estimate how often.
- The audit covers stated numbers, not stated reasoning. A correct number can support a wrong
  conclusion.
- To support an *error rate*, the next step is a hand audit of a random sample of the 252 untraced
  numbers, reported with a Wilson confidence interval.
