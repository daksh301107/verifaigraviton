# VerifAI Hard Policy

This document lists the **hard policy rules**: the things an agent is never allowed to do, no matter how low the risk score, how high its autonomy, or what a person says in the prompt. It is the source of truth for the rules in `engine.js` (`HARD_RULES`). The Settings page shows the same list, grouped.

## 1. What "hard" means

| Layer | What it does | Overridable? |
|---|---|---|
| **Hard policy** (this document) | Short-circuits to **BLOCK** before any scoring. The score is set to 10 and the trigger is recorded as "Hard Policy Check". | No. Not by risk score, autonomy, or human approval in the chat. |
| **Safety layer** (WildGuard + pattern checks) | Detects harmful requests and prompt injection ("ignore previous instructions"). A harmful request on an ordinary business action goes to REVIEW instead of BLOCK. | Only by a human reviewer, for the REVIEW case. |
| **Soft policy** | Adds risk points (bulk email consent, money above ₹10,000, customer-data export, deleting inside the retention window). | Yes. Points feed the weighted score and the decision thresholds. |
| **Risk and authorization** | Reversibility, impact, trajectory, and min(AAL, ACL). | Yes, through the thresholds in Settings. |

A hard rule can fire in three ways, and the decision says which:

1. **Pattern**: the wording matches the rule's pattern (fast, exact, catches the obvious phrasing).
2. **Policy model**: a classifier trained on thousands of reworded examples recognises what the request means, even when the wording is new. It **blocks on its own** only when it is very sure (95%), blocks from **60%** when WildGuard has also flagged the request as harmful, and from **50%** sends the request to a **person** instead of blocking. The three levels are set in Settings → Policy limits.
3. **Lookup list**: a name in the request is on the sanctions register or on a "no consent" recipient list (`policy_data/`).

Two things make these harder to dodge: the **text normaliser** (reads look-alike letters, spaced-out or leetspeak words, base64, ROT13, reversed text and a small Hindi / Hinglish lexicon, and adds the plain reading to what the rules and the model see: `EVALUATION.md` has the measured effect), and the fact that the whole check runs **on the server**, so the page cannot skip it. If a check that should have run does not (WildGuard or a model times out), a non-read-only request is held for a person instead of allowed.

A hard-blocked action is not sent to a human reviewer. The decision says which rule fired and, where one exists, what the person can do instead.

## 2. The rules (28 hard rules)

### Identity & documents

| # | Rule id | What is forbidden | Why | Safe alternative offered |
|---|---|---|---|---|
| 1 | `no_gov_id_edit` | Government ID documents are never edited by agents | Government IDs are legal identity evidence; an agent never alters them. | None — the action is not offered in any form |
| 2 | `id_like_image_edit` | Images flagged as ID-like need verified re-upload | Images that look like an ID need verified provenance. | Ask the user to upload a fresh photo through the verified profile flow. |
| 3 | `no_official_document` | Official-looking documents (offer/appointment/experience letters, certificates, marksheets, ID cards, payslips, NOCs) are not generated for a real organisation, with or without the word "fake" | Job, visa and loan fraud rely on credential documents produced outside the issuer. | I can produce a generic, clearly-labelled SAMPLE/TEMPLATE instead — placeholder names, no real company, and a "SAMPLE — not a real document" watermark. |
| 4 | `no_impersonation` | An agent does not impersonate a person, official or institution (CEO, bank, police, tax or government office) | Acting as someone else deceives the recipient and defeats accountability. | Send it under the agent's own name and say it is sent on behalf of the person. |
| 5 | `no_kyc_bypass` | Identity verification (KYC/AML) is never skipped, waived or worked around | KYC/AML checks are a legal precondition for onboarding and payments. | Route the customer to the normal verification flow and continue once it is complete. |

### Guardrail & oversight

| # | Rule id | What is forbidden | Why | Safe alternative offered |
|---|---|---|---|---|
| 6 | `no_audit_tamper` | Audit logging cannot be disabled or erased by an agent | The ledger is the evidence that the system behaved; an agent cannot erase it. | None — the action is not offered in any form |
| 7 | `no_policy_override` | An agent cannot disable, weaken or override the guardrail, its thresholds or its policies | The agent cannot change the rules that govern the agent. | Ask a policy owner to change the rule through the Settings page, where the change is recorded. |
| 8 | `no_privilege_escalation` | An agent cannot raise its own permissions, autonomy level or access | Autonomy is granted by people, never self-assigned. | Request the permission from an administrator; it is granted outside the agent. |
| 9 | `no_approval_bypass` | Required human approval or review is never skipped or auto-approved | A held action needs a person; the agent cannot approve its own work around that. | Submit the action normally; a held action waits in the approval queue for a person. |
| 10 | `no_self_approval` | An agent or user does not approve their own payment, expense, refund, claim or loan | Segregation of duties: nobody approves a payment, refund or claim for themselves. | Route it to a different approver. |
| 11 | `no_record_backdating` | Records, invoices and approvals are not back-dated, pre-dated or given a false timestamp | Records must show when things actually happened. | Create a new dated entry that references the original and explains the correction. |

### Money & finance

| # | Rule id | What is forbidden | Why | Safe alternative offered |
|---|---|---|---|---|
| 12 | `no_structuring` | Payments are not split or timed to stay under an approval or reporting threshold | Splitting payments to stay under a limit is a recognised money-laundering pattern. | Make one payment and send it through dual approval, or ask compliance to approve the schedule. |
| 13 | `no_sanctions_evasion` | No payments to sanctioned or blacklisted parties, shell entities or untraceable channels (mixers, hawala) | Dealing with sanctioned parties or untraceable channels is prohibited. | Stop and refer to compliance; do not proceed without a documented clearance. |
| 14 | `no_off_books` | No off-the-books, double-booked or tax-evading entries (cash without invoice, two sets of books, fake expenses) | Books must reflect real transactions; hiding income or tax is fraud. | None — the action is not offered in any form |
| 15 | `single_transaction_cap` | No single agent-initiated money movement above ₹1,00,00,000 | A single agent-initiated transfer has an absolute ceiling regardless of approvals in the chat. | Have a finance officer perform it directly, or split into approved tranches through the finance team. |

### Data & credentials

| # | Rule id | What is forbidden | Why | Safe alternative offered |
|---|---|---|---|---|
| 16 | `no_credential_exposure` | Passwords, API keys, tokens, private keys, OTPs and card numbers are never revealed, sent, exported or posted | Secrets are never shown, sent or stored where others can read them. | Use the secure reset or rotation flow, which never exposes the secret. |
| 17 | `no_pii_external_share` | Customer, employee or health data is not sent to personal email, public links or unapproved third parties | Personal data may not leave the organisation for unapproved destinations (data-protection law). | Share through the approved data-sharing process, with the data owner's sign-off, and mask identifiers first. |
| 18 | `no_unmask_pii` | Masked, redacted or anonymised personal data is not de-anonymised or re-identified | Masked or anonymised data must not be re-identified. | Ask the data owner to approve access to the original data through the normal access process. |
| 19 | `no_covert_surveillance` | No covert tracking, monitoring or recording of a person (secretly, without their knowledge or consent) | Monitoring people without their knowledge breaches privacy and consent. | Use disclosed, consent-based monitoring that follows written policy. |

### Harm, deception & fairness

| # | Rule id | What is forbidden | Why | Safe alternative offered |
|---|---|---|---|---|
| 20 | `no_malicious_code` | No malware, ransomware, phishing, exploits, DDoS or credential-harvesting content | Tools for attacking systems or people are never produced or run. | For security testing, use an authorised, scoped engagement with a written agreement. |
| 21 | `no_discrimination` | No decision (hiring, lending, pricing, access) based on caste, religion, gender, race, ethnicity, age, disability, pregnancy, marital status, sexuality or nationality | Decisions may not rest on protected characteristics. | Base the decision on job-related or credit-related criteria and record them. |
| 22 | `no_fake_content` | No fake reviews, testimonials, ratings, endorsements or false advertising claims | Fabricated reviews and testimonials mislead the public (consumer-protection law). | Ask real customers for reviews and publish them as they are. |
| 23 | `no_spam` | No spam or bulk messages to purchased, scraped or non-consenting lists | Unsolicited bulk messaging breaks anti-spam and consent rules. | Send only to a list with recorded opt-in and a working unsubscribe link. |
| 24 | `listed_sanctioned_party` | No payment, transfer or onboarding of a party that is on the sanctions / blacklist register | Dealing with a party on the sanctions or blacklist register is prohibited. | Stop and refer to compliance; do not proceed without a documented clearance. |
| 25 | `unconsented_recipient_list` | No message to a recipient list that the consent register marks as "no consent" | Messages may only go to people who agreed to receive them. | Send only to a list with recorded opt-in and a working unsubscribe link. |

### Irreversible damage

| # | Rule id | What is forbidden | Why | Safe alternative offered |
|---|---|---|---|---|
| 26 | `no_backup_destruction` | Backups, snapshots and recovery points are never deleted, disabled or overwritten by an agent | Backups and snapshots are the way back from any mistake; removing them removes reversibility itself. | Ask the platform owner; expired backups are removed by the retention schedule, not by an agent. |
| 27 | `no_prod_destruction` | No drop, truncate, wipe or format of production databases, servers, storage or the whole system | Dropping production data or systems is catastrophic and cannot be undone by the guardrail. | Perform maintenance on a copy first, with a verified snapshot and a person executing the change. |
| 28 | `no_violence` | No plans, instructions or help to kill, injure, poison, abduct or threaten a person | Help with killing, hurting or abducting a person is never given, whatever the business context. | Contact the emergency services if someone is in danger. |

Of these, 21 text rules are also recognised by the policy model. The other rules do not use it: the three document and ID rules rely on the document checks, `single_transaction_cap` is purely numeric, and the two lookup rules (`listed_sanctioned_party`, `unconsented_recipient_list`) need the lists in `policy_data/`. Limits such as the transaction cap are set in Settings → Policy limits and every change is written to the ledger.

How the pattern rules match: each rule is a pattern over the (typo-corrected) text of the request, or a condition on the parsed action (for example `single_transaction_cap` uses the parsed amount, which understands `₹`, `Rs`, `lakh`, `crore`, `million` and bare large numbers next to a payment verb). For edits **inside a document**, only the instruction is matched, not the document's own text, so a letter that merely contains the word "password" is not blocked.

## 3. Principles behind the list

1. **The agent cannot change its own rules.** Audit tampering, policy override, privilege escalation, approval bypass and self-approval are all hard, because every other control depends on them.
2. **No action that removes the way back.** Deleting backups, snapshots and recovery points, or dropping production data, removes reversibility itself, which is what the rest of the system measures.
3. **Law and regulation set a floor.** KYC/AML, structuring, sanctions, tax evasion, data protection, anti-spam, consumer protection and anti-discrimination are not a matter of risk appetite.
4. **Deception is never an approved use.** Impersonation, forged documents, fake reviews and back-dated records are blocked regardless of intent.
5. **Hard rules are narrow on purpose.** A hard rule can't be talked around, so each one is written to catch the harmful form and leave the benign look-alike alone (for example "Reset the password for user 4421" is allowed; "Email me the admin password" is not). Anything fuzzier belongs in the soft or safety layer, where a person can decide.

## 4. Testing

- `tests/hard_policy_cases.json`: for each pattern rule, prompts that must be blocked and look-alike prompts that must not trigger any rule. `node tests/run_tests.js` runs them with the rest of the engine checks (currently 195 checks).
- `tests/hard_policy_paraphrases.json`: 63 **hand-written, reworded** violations and 20 benign requests that the policy model never trained on. `python3 tests/test_policy_model.py` requires that at least 85% reach a person or a block, at least 80% are matched to the right rule, and at most 3 of 20 benign requests are flagged. Last measured: 62/63 flagged, 57/63 matched to the right rule, 2/20 false alarms at the 50% review level.
- `tests/test_server.py` covers the `/api/policy-check` endpoint and the lookup lists.

Add a case to the first file when you add a pattern rule, and a reworded violation to the second when you find one the model misses.

## 4a. Retraining the policy model

```
python3 training/generate_policy_data.py     # templated positives + benign look-alikes
python3 training/generate_policy_llm.py      # LLM-written paraphrases and same-topic benign requests (needs the key in .env; ~50 minutes)
python3 training/train_policy.py             # writes models/model_policy_intent.joblib and prints the held-out numbers
```

The model scores the chance that a request breaks *some* rule (1 minus the chance it is benign) and names the likeliest rule. It learns from about 8,000 examples, so it generalises to new wording but not perfectly: it can name the wrong rule on an unfamiliar phrasing, which is why a mid-confidence match goes to a person and does not block.

## 5. Adding or changing a rule

1. Add an entry to `HARD_RULES` in `engine.js` with `id`, `group`, `basis`, `title`, `test(a)` and `remediation(a)`. Use `ut(a)` for the text to match so document edits are handled.
2. Add at least two blocking prompts and two benign look-alikes to `tests/hard_policy_cases.json`.
3. Run `node tests/run_tests.js`. Check that ordinary prompts ("Refund order 1234 for Rs 800", "Delete 3 old log entries") still pass.
4. Update this document (the table above is generated from the code, so regenerate it from `HARD_RULES`).
5. Have the policy owner sign off. A hard rule is a business and legal decision, not only an engineering one.

## 6. Known limits

- **The model is not a guarantee.** On unseen paraphrases it reaches a person or a block for about 98% and names the right rule for about 90%, with a few benign requests flagged. A determined user can still find wording that slips through, and a benign request that sounds like a violation (for example hiring "based on interview scores") can be sent to a person. That is why hard rules sit in front of WildGuard, the impact model and the human approval step, not instead of them.
- **Documents.** Each edit is judged by the server on the instruction and the line it touches, using the pattern rules plus the instruction-level WildGuard and policy-model signals. Lines inside the document that talk to an AI are detected (patterns, then the policy model and WildGuard on the likeliest few) and their edits are held, but the document body is not otherwise run through the model line by line.
- **Mostly English**, with typo tolerance and a small Hindi / Hinglish lexicon in the normaliser (92% of 12 test phrases). Other languages are not covered. A translate-first step would fix this and has not been built.
- **Undo inside a document.** Restoring an earlier version ("undo", "change it back") is a normal edit, except that going back past a redaction is blocked as re-identification (`no_unmask_pii`).
- **Thresholds are policy choices.** The ₹1 crore cap, the ₹10,000 dual-approval level and the model's confidence levels are defaults. A policy owner should confirm them; changes are made in Settings and logged.
- **The lookup lists are sample data.** `policy_data/sanctions.csv` and `consent_lists.csv` contain made-up entries. They must be replaced with the real lists before any real use. Matching is by name appearing in the request, so a misspelt name will not match.
- **No reviewer identity yet.** Human decisions are recorded as "human-reviewer", so maker/checker separation is not enforced.

## 7. Rules worth adding once there is data to check against

These need information the prototype does not have, so they are not implemented:

| Candidate rule | What it needs |
|---|---|
| Payments only to verified, pre-registered beneficiaries | A beneficiary register |
| Segregation of duties by real user identity (maker ≠ checker) | Logged-in reviewer identity |
| Translate-first checking for non-English requests | A translation step (an LLM call) |
| No access to data outside the requesting user's department or region | Role and data-classification model |
| No changes during a freeze window | A change calendar |
| Medical, financial or legal advice only with a licensed reviewer | A reviewer role and a topic classifier |
| Rate limits per agent (actions per minute, rupees per day) | A durable per-agent counter shared across sessions |
