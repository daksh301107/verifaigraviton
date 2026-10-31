# Evaluation: what was measured, and what was not

Numbers below were produced by the test files named next to them, on this version. **Almost every test set was written by the team that built the system.** Treat the numbers as "it does what we designed it to do", not as proof it will hold up against outsiders. `tests/run_external_eval.py` exists to fix that (see the end).

## 1. Engine and rules

| What | Result | Where |
|---|---|---|
| Engine regression, including the 20 project test cases and every hard rule's blocked / allowed prompts | 195 checks pass (1 documented divergence, TC17, written for an old cost scale) | `node tests/run_tests.js` |
| Hard rules, pattern layer | all 64 known violations blocked, 0 of 65 benign look-alikes hit a rule | `tests/hard_policy_cases.json` |
| Server behaviour: no verdict from the page, fail-closed, reviewer rules, ledger tampering, tool gateway, agent release, documents, saving, static-file blocking | 66 checks pass | `python3 tests/test_server.py` |
| The real page in Chrome: block, allow, held action + reviewer sign-in, maker is not checker, admin-only settings, document edit, refusal to undo a redaction, tool call, ledger verify, no script or request errors | 28 checks pass (including the sign-in page, both doors, sign-out and the model picker) | `python3 tests/ui_smoke.py` |

## 2. Policy model (reworded violations)

Trained on about 8,100 examples (templated + LLM-written). Measured on **63 hand-written reworded violations and 20 benign requests it never trained on**:

| Threshold (chance it breaks some rule) | Flagged (any rule) | Right rule named | Benign wrongly flagged |
|---|---|---|---|
| 50% (sends to a person) | 62 / 63 | 57 / 63 | 2 / 20 |
| 60% (blocks if WildGuard agrees) | 61 / 63 | 56 / 63 | 1 / 20 |
| 85% | 46 / 63 | 43 / 63 | 1 / 20 |

The default "block on the model alone" level is 95%, so a model-only block is rare, and a middling match goes to a person. With 63 examples one miss moves the rate by 1.6 points, so these are rough.

## 3. Adversarial input (disguised violations)

64 known violations, each disguised in 12 ways, plus 12 Hinglish / Hindi phrases. "Blocked" means a hard rule or the safety layer returned BLOCK. Patterns only (the server's models add to this). Columns: without / with the text normaliser.

| Disguise | Blocked without | Blocked with |
|---|---|---|
| plain (control) | 100% | 100% |
| mixed case | 100% | 100% |
| leetspeak (`dr0p th3 pr0duct1on`) | 9% | 100% |
| spaced letters (`d r o p t h e …`) | 0% | 97% |
| look-alike letters (Cyrillic) | 2% | 100% |
| zero-width characters | 2% | 100% |
| base64 | 0% | 100% |
| ROT13 | 0% | 97% |
| reversed text | 0% | 94% |
| roleplay framing | 100% | 100% |
| "ignore previous instructions" prefix | 100% | 100% |
| polite wrapper | 97% | 97% |
| Hinglish / Hindi (12 phrases, small lexicon) | 0% | 92% |

Benign requests hitting a hard rule: 0 of 65 both with and without the normaliser (`node tests/run_adversarial.js`).

The disguises are **ones we thought of**. A different obfuscation, a language other than the small Hindi lexicon, or a multi-turn setup (a harmless first message that defines a code word) is not covered. Without the normaliser an unrecognised request still goes to a person (an unknown action is held), which is why the "stopped" rate looks high even without it; the table counts BLOCK to measure the rules themselves.

## 4. Impact model and its "sureness"

| What | Result |
|---|---|
| Accuracy on two hand-written test sets (83 and 60 prompts) | 92.8% and 96.7% |
| Calibration on 143 hand-written prompts | mean "sure" 90.3% vs 94.4% right; average gap (ECE) 5.5 points; when it said 90–100% sure it was right 99% of the time (n = 104), but in the 50–80% range there are only 22 prompts |

The model is roughly calibrated on this set, slightly under-confident in the middle. The UI says "How sure the models are" and states that this is not a probability that the verdict is right. The score and the risk weights are hand-set; no outside party has calibrated them.

## 5. What each AI component adds (measured)

`python3 tests/measure_wildguard.py` runs the same prompts through the decision engine four ways: **A** rules only, **B** + the policy model, **C** + WildGuard, **D** both (what the product does). Cells show *blocked / stopped* (stopped = blocked or sent to a person). Higher is better for violations.

| Violations | n | WildGuard alone flags | A rules | B + policy model | C + WildGuard | D both |
|---|---|---|---|---|---|---|
| Known violations (the ones the rules were written for) | 64 | 61% | 100% / 100% | 100% / 100% | 100% / 100% | 100% / 100% |
| Reworded violations (hand-written, unseen) | 63 | 62% | 6% / 59% | 60% / 100% | 43% / 89% | 84% / 100% |
| Disguised violations (leetspeak, base64, look-alikes, Hinglish, ...) | 78 | 62% | 99% / 100% | 99% / 100% | 99% / 100% | 99% / 100% |

Ordinary requests (lower is better; cells are *wrongly blocked / sent to a person or blocked*):

| Ordinary requests | n | WildGuard alone flags | A rules | B + policy model | C + WildGuard | D both |
|---|---|---|---|---|---|---|
| Look-alikes of the rule topics | 65 | 3% | 6% / 45% | 6% / 48% | 6% / 46% | 6% / 49% |
| Routine, low impact (read, tickets, small edits) | 40 | 8% | 0% / 5% | 0% / 5% | 0% / 12% | 0% / 12% |
| Routine, medium impact (refunds, payments, record edits) | 40 | 5% | 5% / 52% | 5% / 52% | 5% / 55% | 5% / 55% |

What this says:

* **Both AI components earn their place on reworded requests**: rules alone block 8% (60% blocked or held), the policy model lifts that to 60% blocked, WildGuard to 44%, and together 84%, with 100% at least held for a person. Each one catches things the other misses.
* **WildGuard on its own flags about 61-62% of the violations**, so it is a real signal here, not only a content filter. It is **not enough alone**: it misses 4 in 10, and it flags about 3-8% of ordinary requests, which adds some requests sent to a person (low-impact routine requests: 5% with rules only, 12% with WildGuard).
* **Known and disguised violations are already stopped by the rules** (the normaliser does the work for disguises), so the models add little there.
* **Friction is real.** About half of medium-impact routine requests (refunds, payments, record edits) go to a person at the default autonomy of 6, and about 5% are blocked. That is the policy as set (see below), not a measurement error, but a policy owner should decide whether it is right.

Found while measuring, and fixed: any request containing the word "email" was treated as a mass mailing (because "mail" is inside "email") and blocked; "generate a report of all transfers" was treated as a money transfer. Wrongly blocked ordinary requests fell from 11% to 5%.

**Decisions this leaves with the policy owner** (see `DESIGN_DECISIONS.md`): bulk email to a consenting list is always blocked, because a sent email cannot be recalled (so even a normal newsletter needs an exception path); transfers of roughly ₹50,000 and above are blocked by the risk score instead of going to a person; medium-impact changes go to a person at default autonomy.

Limits of this measurement: the same team wrote the violations, the look-alikes and the rules; the "routine" requests come from our LLM-generated impact data, so they read more like business requests than a real user's; and the result is for a fresh session with no earned autonomy.

### Llama 3.1 8B: tool choice

`python3 tests/measure_llama.py` gives Llama the same instruction the server does and 24 requests written by us (21 that need a tool, 3 that should not use one). Results in `tests/results/llama_tools.json`.

| Measure | Result |
|---|---|
| Valid JSON answer | 24 / 24 |
| Right tool | 23 / 24 |
| Right tool and every argument right (amounts, counts, table, backup flag, ids) | 23 / 24 |
| Requests that need a tool: right tool, all arguments right | 21 / 21 |
| Requests that should not use a tool: handled correctly | 2 / 3 |
| Speed | about 4.5 seconds per request on the MacBook |

The one miss: "Cancel Friday's lunch plans" made it pick `send_email`. The guardrail would still judge that call, but it shows the model sometimes grabs a tool when none fits. The requests are simple and written by the team, so this is a sanity check. The larger test in 5b (170 requests per set, written by another LLM) is the fairer number. Llama's drafted answers and its refusal explanations have not been scored: there is no ground truth for them, and they never decide anything.

> **Note:** the Llama figures below are for the *local* Llama 3.1 8B. The agent in use is now Groq `gpt-oss-20b`. On the fresh set 3 (168 requests) it chose the right tool **86%** of the time (CI 80-90%), picked the right tool and every checkable argument 71%, and declined correctly when no tool fits 82% (23/28). That set was written by the same model family, so the number is, if anything, optimistic. Its misses include answering "none" to clear transfer requests. The guardrail judges whatever call it makes, so a wrong or missing tool call is checked or simply does nothing.

### 5b. A larger test on requests nobody on the team wrote

A hosted LLM (Groq `gpt-oss-20b`: not Llama or WildGuard, the models under test, but the **same model that wrote the policy and impact models' training data**) wrote three sets of test requests: set 1 and a fresh set 2, each with **170 tool requests** and **180 requests labelled allow / review / block** by what a reasonable company would want (60 of each label). `training/generate_eval_sets.py` makes them; `python3 tests/eval_large.py all [--set 2]` scores them (about 18 minutes per set; it starts its own server and touches none of your records). Intervals are 95% confidence intervals (Wilson).

The hand-written held-out sets in sections 2 and 4 (written by us, no LLM) are the more independent check. Set 1 is where the first run found problems (listed below), so it is a *development* set. Set 2 was generated afterwards and not looked at before the fixes, so it is the fair number.

**The whole system (allow / review / block):**

| | Set 1, first run | Set 1, after fixes (not fair: fixes were made looking at it) | **Set 2, fresh** |
|---|---|---|---|
| Prohibited requests **let through (ALLOW)** | 0 / 60 | 0 / 60 | **0 / 60** (95% CI 0-6%) |
| Prohibited requests stopped (blocked or held for a person) | 100% | 100% | **100%** (60/60) |
| Prohibited requests blocked outright | 97% (58/60) | 97% (58/60) | **98%** (59/60) |
| Risky-but-legitimate requests held or blocked | 87% | 95% | **98%** (59/60) |
| Ordinary requests allowed | 57% | 58% | **55%** (33/60, CI 42-67%) |
| Ordinary requests **wrongly blocked** | 15% (9/60) | 0% | **3%** (2/60, CI 1-11%) |
| Ordinary requests held for a person | 28% | 42% | **42%** (25/60, CI 30-54%) |
| Exact agreement with all three labels | 64% | 67% | **68%** (CI 61-75%) |

**Llama 3.1 8B choosing a tool:**

| | Set 1, first run | Set 1, after a stricter instruction | **Set 2, fresh** |
|---|---|---|---|
| Valid JSON | 99% | 99% | **99%** (169/170) |
| Right tool, all 170 requests | 89% (151) | 94% (159) | **95%** (162/170, CI 91-98%) |
| Right tool when one is needed | 94% | 96% | **97%** (136/140, CI 93-99%) |
| Declines when no tool fits | 67% (20/30) | 83% (25/30) | **87%** (26/30, CI 70-95%) |
| Right tool and every checkable argument right | 71% | 73% | **76%** (106/140, CI 68-82%) |

What this says:

* **Safety held up.** Across both sets, **none of the 120 prohibited requests was allowed**, and about 98% were blocked outright. This includes requests worded indirectly. The wrongly allowed cases that did appear were all in the *risky-but-legitimate* group (for example "change a user's role to admin"), which is now sent to a person.
* **The cost is friction.** About **42% of ordinary requests are held for a person**. The biggest cause is WildGuard: on set 2 it accounts for 14 of the 27 ordinary requests that were held or blocked. WildGuard flags routine words ("audit report", "KYC status", "reset password"). This is the main thing a policy owner should decide next (see `DESIGN_DECISIONS.md`).
* **Llama is good at picking the tool and weaker at the details.** It rarely picks the wrong tool, but it **invents arguments**: asked to notify one person, it filled in 205 or 512 recipients; it sometimes drops or adds a backup flag. About a quarter of requests have a wrong checkable argument. Because the guardrail judges the structured call, a made-up number changes the verdict. A fix worth doing: refuse any numeric argument that does not appear in the person's request.
* **Part of the "argument" gap is the test, not Llama.** Llama writes `vendor-1593` where the test expects `1593`; the scorer now treats those as equal. The set-1 "should not use a tool" cases include several nonsense lines ("id=8372 amount=3500") that the generator labelled "none" but are arguably ambiguous.

Problems this test found, now fixed (each has a regression test in `tests/run_tests.js`):

* WildGuard alone could BLOCK a routine read ("pull the audit report", "check the KYC status"). It now needs support from a rule, the policy model or a harmful-content keyword to block; otherwise a person decides.
* A temporary password sent through a secure channel after a reset was blocked as credential exposure.
* "Recap of the campaign launch" in a meeting request was treated as a mass mailing.
* Changing a user to admin, revoking admin access, and approving a purchase order were not recognised as needing a person.

Limits of this test: the labels come from the instructions given to the generator, so they encode one plausible policy and have some noise (some "review" and "allow" labels are debatable); the same LLM family wrote the policy model's training data, so set 2 is not fully independent of it; it runs in a fresh session with default autonomy; and no human outside the team labelled anything.

## 6. What was NOT measured

* **Prompts written by people who did not build it.** Use `tests/run_external_eval.py`: give `tests/external_eval_template.csv` to 3–5 people, have each add ~25 rows (expected: allow / review / block), run it against the live server. It reports accuracy per tester, harmful requests let through, harmless ones over-blocked, and every disagreement. Report those numbers, not an average.
* **Multi-turn and long-context attacks, and non-English languages beyond the small lexicon.**
* **Latency and load.** WildGuard and Llama each handle one request at a time on a laptop.
* **The risk weights and thresholds, the ₹ limits, and the autonomy penalties.** They are defaults for a policy owner to set (see `DESIGN_DECISIONS.md`). Autonomy now loses less per bad action (block −1.2, review −0.3, floor 1.5) than before, because the old values locked the agent out after one or two blocks.
