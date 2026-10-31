# Design decisions that need team sign-off

These are choices made while building the console. `CLAUDE.md` lists several of them as open; none is final.

## Trajectory Risk Engine (the four open gaps)
- **Normalisation.** `C` = cumulative risk ÷ budget 30, `W` = decayed window average ÷ 10 (λ 0.7, window 5), `D` = sequence
  risk ÷ 12, `V` = change in window risk ÷ 5; each clipped to [0, 1].
- **Weights.** wA 0.25, wB 0.30, wC 0.25, wD 0.20. Chosen so the recent window counts most; not derived from data.
- **Pairwise table behind `D`.** Hand-written transitions (read→export 3, export→comms 3, update→delete 2, …, default 0.3)
  plus 1.5 per consecutive action on the same target. Needs review by someone who knows the real risk of each pairing.
- **Replace or extend the structuring check.** Implemented as *extend*: the structuring pattern (≥3 actions to one target,
  total above ₹900 in a window of 10) is detected separately and lifts an ALLOW to REVIEW. TR is shown for every action.
- τ₁ 0.35 and τ₂ 0.65 are placeholders.

## Thresholds
Review at weighted score 2.0, block at 5.0. These were set so 19 of the 20 synthetic cases match; they are not calibrated
against real data. TC17 cannot match (it assumes the old rupee scale).

## Authorization
ACL and AAL are numbers on a 0–10 scale. **ACL** (capability for this action) = 5×classification confidence + 3 if an undo path
exists + up to 2 for narrow scope. **AAL** (autonomy earned) starts at 6; +0.4 per clean action in a row (max 5), −2.5 per
block and −0.8 per review in the last 5, −2 for a flagged unsafe request, −3×last trajectory risk. Required level per action
type is a lookup in `engine.js` (`ACTIONS`). None of this comes from the Cloud Security Alliance framework; it only follows
its idea that autonomy is earned.

## Policy
Editing a government ID value or photo is a hard block; **masking** an ID number in a document is allowed (it protects the
data). Confirm that matches the intent of `no_gov_id_edit`.

## Model signals
The impact model (retrained, see `training/README.md`: 92.8% / 96.7% on two hand-written test sets, against 43% / 47% for the
original and 49% / 58% for the rules alone) now leads the impact estimate when it is at least 60% sure: weight 0.5, 0.65 or 0.8
by confidence, blended with the rules. It can raise a score freely but lower it by at most 2.5. It is not used for read-only
requests or document edits, and is ignored below 60% confidence. `intent_match` is only used when a real declared intent is supplied; the UI has no such
field, so it is currently unused.

## WildGuard flags
WildGuard is a content-safety model, so it can also flag plain admin commands (it flagged "delete the user account
permanently, no backup"). Decision: on a recognised business operation (delete, update, money, comms, create, export) its flag
sends the action to **REVIEW** and lowers the next AAL; it **blocks** outright only for non-operational requests (for example a
harmful question), keyword-detected injection, forgery or other harmful content. Checked on a set of ordinary operations
(cancel a subscription, purge stale logs, refund per policy, delete with a backup): WildGuard did not flag any of them.


## Open decisions found by measuring (EVALUATION.md section 5)

1. **Bulk email is always blocked.** A sent email cannot be recalled, so reversal cost is unbounded and the verdict is BLOCK, even for a newsletter to a consenting list. Option: send bulk email to a list that the consent register marks as consented to REVIEW instead.
2. **Larger transfers are blocked, not held.** A transfer of about ₹50,000 scores above the block threshold, so no person can approve it from the page. Option: raise the block threshold, or send money above the dual-approval amount (₹10,000) to REVIEW up to the hard cap.
3. **Medium-impact changes go to a person at default autonomy.** About half of routine refunds, payments and record edits need a person in a fresh session. Option: start autonomy higher for a trusted agent, or lower the review threshold.
4. **WildGuard adds some friction.** It flags about 3-8% of ordinary requests, which become reviews. Option: use it to raise a request to review only when it agrees with another signal.
5. **WildGuard is the largest source of friction.** In the large test it explains about half of the ordinary requests that were held or blocked (14 of 27 on the fresh set). Options: let a WildGuard flag count only when another signal agrees; keep it only for non-business text; or accept the friction. It also adds real value on reworded violations, so this is a trade-off.
6. **Llama invents arguments.** Add a check that every number in a tool call appears in the person's request, and send a call with an unsupported number to a person.
7. **The agent is hosted (Groq) and the safety check is local.** The 🤖 button, tool choice and explanations send text to Groq; nothing else does. Option: use the local Llama (slower, plainer, fully private) for sensitive use. The previous tool-choice measurements were for local Llama.
8. **Llama Guard is available but not measured.** Run `./start.sh --llamaguard`, switch to it in Settings → Models, then `python3 tests/validate_wildguard.py` to measure it before relying on it.
