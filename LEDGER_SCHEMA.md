# Evidence Ledger schema

One JSON object per line in `records/ledger.jsonl`. Append-only. **Rows are written by the server**, never by the browser. Rows written before signing was added (`v` absent) are checked by hash only.

| Field | Type | Meaning |
|---|---|---|
| `seq` | int | Position in the ledger. Continues across restarts. |
| `ts` | ISO-8601 string | When the decision was committed (UTC). |
| `agent` | string | Who proposed the action (`human-reviewer` for approvals of held edits). |
| `action` | string | The prompt, or `Document "<name>": <instruction>`. |
| `verdict` | `ALLOW` \| `REVIEW` \| `BLOCK` | Decision Engine outcome. |
| `tier` | `LOW` \| `MID` \| `HIGH` | Risk tier. |
| `score` | number 0–10 | Weighted risk score. |
| `trigger` | string | The stage that decided. |
| `prev` | string | `hash` of the previous entry (`00000000` for the first). |
| `hash` | string | Hash of `prev` + the JSON of the eight fields above, in that order. |
| `alg` | `sha256` \| absent | Hash algorithm. Absent means FNV-1a (entries written before SHA-256). |
| `actor` | string | **v2 rows.** Who caused it: a signed-in user id (`ravi`), or `guest`. For a human review it is the reviewer. |
| `decision_id` | string | **v2 rows.** Links a row to the decision it belongs to (a request, and the review of that request). |
| `v` | `2` | **v2 rows.** Marks rows whose hash also covers `actor`, `decision_id` and `v`. |
| `sig` | hex string | **v2 rows.** HMAC-SHA256 of `hash`, made with a key kept in `~/.verifai/` (not in the records folder). |

Per-action detail is in `records/actions/<seq>_<verdict>_<slug>.json` and `.html`; edited documents are in
`records/outputs/`.

**Verifying.** `Verify chain` (Ledger tab, or `GET /api/ledger/verify`) re-hashes every entry, checks each `prev`, checks each v2
signature, and compares every entry with the checkpoint log `~/.verifai/anchors.log`, which the server appends to on each write.
Editing a field, removing an entry, or cutting entries off the end is reported with the first bad `seq`.
The hash for v2 rows is `sha256(prev + JSON({seq, ts, agent, action, verdict, tier, score, trigger, actor, decision_id, v}))`
(compact JSON, that key order). **Limits:** someone who controls this Mac account can read the key and the checkpoint log and
rebuild everything. Anchoring the latest hash outside this machine (`VERIFAI_ANCHOR_URL`, or pasting it into an email) is what
closes that gap. "Replay" re-scores a stored action in the browser and confirms the same verdict. Personal identifiers
(emails, phones, Aadhaar, PAN, cards) are masked in `action` before it is hashed and written.
