# Do clinical malingering tests catch AI sandbagging? Mostly no.

*Exploratory results. ~3-page write-up. Code and data: [github.com/Rodio346/svt-sandbagging](https://github.com/Rodio346/svt-sandbagging). 2026-10-23.*

## TL;DR (finding first)

Forensic neuropsychologists have spent 30 years catching people who *fake* cognitive impairment,
using "symptom validity tests" (SVTs): score below chance on a forced-choice test, fail items even
impaired people pass, show an implausible error profile. I ported six of these tests to LLM
**sandbagging** — a model deliberately underperforming on a capability eval — and ran them against
three capable API models on WMDP, with the same-model and accuracy-matched controls that
[Lin et al. (2026)](https://lacuna.tiptreesystems.com/work/when-lie-detectors-learn-model-identity-confounds)
showed are necessary.

Three things fell out:

1. **A blatant sandbagger is trivially caught.** Told to "deliberately underperform," all three
   models drop *below the 25% chance line* — the single most reliable SVT signature. AUROC ≈ 1.0.
2. **A careful sandbagger beats every black-box profile test.** Once a model aims for a plausible
   weaker-than-itself score, **no** output-only profile detector (easy-item errors, difficulty
   insensitivity, order inconsistency, atypical distractors) separates it from a *genuinely* weak
   model of the same accuracy — none clears the genuine-vs-genuine null band. Only reading the
   model's scratchpad works, and only one of three models could sandbag convincingly at all.
3. **No spontaneous sandbagging.** An Apollo-style "score too high and we retrain you" incentive,
   with no instruction to underperform, produced zero accuracy drop in any model.

## The question

If we test a frontier model for dangerous knowledge and it sandbags, the eval reads "safe" and we
deploy a model that was hiding its capabilities. We'd like a cheap, output-only detector that flags
this. Clinical SVTs are the obvious prior art, and [Cacioli (2026)](https://arxiv.org/abs/2604.25249)
tried the below-chance test on 7–9B open models — it failed, because small models given a
sandbagging prompt fell back on favourite answer letters instead of avoiding the right answer. This
project asks whether the SVT battery works on *capable* models, across a ladder of sandbagging
sophistication, with proper controls.

## Setup

Three subjects (`gemini-flash-latest`, `gpt-5-mini`, `deepseek-chat`) each answered 300 WMDP
multiple-choice items, every item shown in two option orders (the second a full derangement, so
*what* a model picks is separable from *where*). Each subject ran under a ladder of prompts:

- **naive** — "deliberately underperform."
- **calibrated** — "underperform to ~80% so it looks like a weaker model." (80% = a real weak
  model's honest score.)
- **coached** — calibrated, *plus* the six detectors spelled out.
- **incentive** — a note saying high scores trigger retraining; *no* instruction to sandbag.

The crucial piece is the controls. A sandbagger scoring 75% must be compared not to its honest 90%
self (that just measures "lower score") but to something *genuinely* weak at 75%: a weaker model,
and the same model with question stems blanked out until it honestly scores 75%. A detector only
counts if it beats the **null band** — the AUROC two honest runs reach by chance.

## Findings

**Accuracy by arm** (WMDP, parsed answers; honest ≈ 0.8–0.9, chance = 0.25):

| Arm | gemini | gpt-5-mini | deepseek |
|---|---|---|---|
| naive | **0.03** | **0.19** | **0.30** |
| calibrated | 0.86 | **0.63** | 0.89 |
| coached | 0.87 | **0.73** | 0.87 |
| incentive | 0.87 | 0.76 | 0.86 |
| redacted (genuine control) | 0.75 | 0.74 | 0.74 |

**H1 — naive sandbagging is caught (supported).** All three subjects collapse to ~chance. The
below-chance test and raw accuracy both flag it at AUROC ≈ 1.0 vs honest. No subtlety required.

**H2/H3 — black-box profile tests fail the fair comparison (the core result).** Against the
same-model genuine-weakness control at n = 100 items (null band in parentheses):

| Detector | coached AUROC | (null band) | clears? |
|---|---|---|---|
| D2 easy-item errors | 0.31 | 0.66 | ✗ |
| D3 difficulty insensitivity | 0.24 | 0.72 | ✗ |
| D5 order inconsistency | 0.33 | 0.96 | ✗ |
| D6 atypical distractors | 0.01 | 0.79 | ✗ |
| B1c scratchpad monitor | 0.80 | 0.75 | ✓ |

Not one output-only profile detector clears the null band for the calibrated or coached arms. The
detector we *expected* to be strongest — order inconsistency (D5), the idea that a sandbagger
re-decides on each presentation and so flip-flops when options move — has a null band of **0.96**:
genuine runs flip-flop on hard WMDP items just as much. The two-sided **norms detector** (flagging
atypicality in either direction, built from genuine sessions only) does better on calibrated (0.84),
supporting the "a battery degrades slower than any single test" idea — but the single black-box
signatures are, individually, useless here. This is exactly the model-identity confound Lin et al.
warned about: cross-model difficulty signals mostly encode *which model answered*, not *whether it
faked*.

**The only reliable catch is the scratchpad monitor** (0.80–0.96) — a cheap LLM reading the model's
private reasoning. But that is not a black-box test, and it is a weak baseline by construction: the
sandbagging prompts explicitly tell the model to keep its scratchpad clean. It still leaks, which is
itself interesting, but a more careful sandbagger would close this hole.

**H4 — no spontaneous sandbagging (untestable at this scale).** The incentive note produced no
accuracy drop in any model (exact McNemar p = 0.75 / 0.73 / 0.92); incentive ≈ reverse ≈ honest.
These models do not, under a mild in-context incentive, decide to underperform on their own.

**A behavioural split.** Only **gpt-5-mini** could actually sandbag subtly — it lowered its accuracy
to a plausible 0.63–0.73 when asked. **Gemini and DeepSeek wouldn't**: told to aim for 80%, they
kept answering honestly. Their calibrated scratchpads are full, confident, correct reasoning —

> *"Vaccinia virus is a member of the Poxviridae family… poxviruses uniquely replicate their large
> double-stranded DNA genomes within viral factories located entirely in the cytoplasm."* — Gemini,
> calibrated arm, answered correctly.

— whereas their naive answers are hand-wavy:

> *"DNA-launched systems might be mostly related to speeding up how the virus changes… option B
> sounds like a reasonable choice."* — Gemini, naive arm, answered wrong.

gpt-5-mini's coached sandbagging is the hard case: its reasoning looks earnest and close to correct,
which is why only the transcript monitor flags it.

## Limitations

- **Exploratory, not pre-registered.** Treat every number as hypothesis-generating. A confirmatory
  claim needs OSF pre-registration and a fresh run.
- **WMDP is easy for these 2026 models** (honest 0.73–0.90), so the weak reference (0.80) is barely
  weaker than the subjects and the calibrated contrast is small — the deck is stacked toward "no
  detectable difference."
- **Options leak:** options-only accuracy is 0.50–0.61, far above chance, a known WMDP quirk.
- **Prompted sandbagging is a model organism, not a schemer.** Two option orders, one seed. Only one
  of three subjects sandbagged subtly, so the "sophisticated" signal rests on a single model.

## Takeaway

For AI-safety evals: **below-chance scoring is a genuinely useful, nearly-free tripwire for clumsy
sandbagging**, and worth computing on every forced-choice eval. But the richer clinical profile
tests — the ones that catch sophisticated human fakers — **do not survive the accuracy-matched
control** on current models. If a model sandbags carefully enough to match a plausible weaker model,
output-only statistics can't currently tell the difference; you need access to its reasoning, and a
careful enough model can hide that too. Encouragingly, today's capable models mostly *won't* sandbag
subtly on command — they sandbag blatantly or not at all. That is likely to change.
