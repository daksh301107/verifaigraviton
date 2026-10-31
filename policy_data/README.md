# Policy lookup data

Files the guardrail checks a request against. **The contents here are sample data.** Replace them with the real lists owned by your compliance team; the server re-reads them whenever the file changes, so no restart is needed.

| File | Used by | Columns |
|---|---|---|
| `sanctions.csv` | rule `listed_sanctioned_party`: a payment, transfer or onboarding naming a listed party is blocked | `name`, `reason`, `source` |
| `consent_lists.csv` | rule `unconsented_recipient_list`: a message to a list marked `no_consent` is blocked | `list_name`, `status` (`consented` or `no_consent`), `note` |

If a file is missing or empty the check reports "not checked" in the decision and a payment to a named party gets a few extra risk points, so the gap is visible instead of silently passing.

| File | Used by | Columns |
|---|---|---|
| `users.csv` | sign-in and approvals: who can approve held actions (`reviewer`) and change policy limits (`admin`) | `id`, `name`, `role`, `salt`, `pin_hash` |

**Sample users (change these before any real use):** `asha` (requester) PIN 1111, `ravi` (reviewer) PIN 2222, `meena` (admin) PIN 3333. Add or change a user with `python3 scripts/add_user.py <id> "<name>" <requester|reviewer|admin>` (it asks for the PIN). A reviewer can never approve their own request.

`users.sample.csv` holds the same demo accounts and is used only when `users.csv` does not exist (for example in a fresh unzip). `scripts/add_user.py` creates `users.csv`.
