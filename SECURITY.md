# Security notes

What this system does and does not protect against, as of this version. It is a hackathon-grade prototype that has been hardened in the places below; it is **not** a production security product.

## What is enforced, and where

| Property | How | Test |
|---|---|---|
| The browser cannot change a verdict | The engine runs on the server (`engine_worker.js`). The page sends text and draws the answer. | `tests/test_server.py` |
| A safety check that did not run does not mean "safe" | WildGuard / policy model / impact model failures are listed in `degraded`; a non-read-only action is then held for a person. A draft that WildGuard cannot check is withheld. | "FAIL CLOSED" checks |
| Two sign-in doors | The sign-in page has a User door and an Admin door. The server (not the page) refuses an admin account at the User door and a user account at the Admin door. | door checks |
| Approvals are accountable | A held request is first decided by the person who made it (recorded as "approved by the requester"), or sent to a reviewer. The reviewers' decision (`/api/review`, a signed-in reviewer with a PIN, PBKDF2, 5 wrong tries lock the account for a minute) can never be made by the requester, including a guest who signs in as a reviewer in the same window (maker ≠ checker). Both paths are ledgered under a name. **Trade-off:** letting a requester approve their own held action is weaker than mandatory maker-checker; use "Ask a reviewer" for anything that matters. |
| Settings are controlled | `/api/config/set` needs an admin and every change is a ledger row. | settings checks |
| The ledger is tamper-evident | Server-written rows: SHA-256 chain + HMAC signature with a key in `~/.verifai/` + a checkpoint log in `~/.verifai/anchors.log`. Editing a row, or deleting the end of the file, is detected by `/api/ledger/verify`. | ledger tamper checks |
| Secrets are not served | Only `index.html`, the scripts, `style.css` and `vendor/` are served. `.env`, `records/`, `models/`, `policy_data/`, the Python source are 404. | static checks |
| Other web pages cannot use the API | `Host` must be localhost, `Origin` must match, every write needs the session header (which forces a preflight the server never answers), the server listens on 127.0.0.1. | host check |
| Agent answers and tool calls cannot skip the guardrail | `/api/agent/run` and `/api/tool-call` decide first, then release. `/api/agent/output|feedback` (which returned drafts unconditionally) were removed. Tool arguments are validated and the tools only touch a sandbox. | agent / tool checks |
| Personal data is not stored in clear | Emails, phone numbers, Aadhaar, PAN and card numbers are masked in ledger text, saved reports and action files (`VERIFAI_STORE_RAW=1` turns this off). The edited document you download is your own output and is saved as is. | |
| Files are private | `records/`, `~/.verifai/` and `.env` are created or set to owner-only permissions. | `ledger file is private` |

## What is NOT protected (read this before relying on it)

1. **Whoever controls this Mac account can rewrite history.** They can read the signing key in `~/.verifai/` and the checkpoint log next to it. Anchoring the latest hash somewhere you do not control closes the gap: set `VERIFAI_ANCHOR_URL` to a webhook, or paste the hash from the Ledger tab into an email or ticket now and then.
2. **Sign-in is a PIN, and the session id is kept in the browser tab (sessionStorage) so a reload keeps you signed in.** Anything that can run script in that tab could read it. It exists to make approvals accountable in a demo, not to resist a determined attacker with local access. The sample users have sample PINs (`policy_data/README.md`). Replace them with `scripts/add_user.py`.
3. **Nothing real is gated yet.** The tools are a sandbox. To guard real tools, wrap them the way `vf_tools.py` does (structured arguments in, judged by `/api/tool-call`) and never give the agent a path to the real system that skips the gateway.
4. **Wording-based rules can be evaded.** The text normaliser, the policy model and WildGuard raise the cost, but `EVALUATION.md` shows what still gets through. Multi-turn setups (a harmless first message that defines a code word) are not handled.
5. **Documents.** Lines that talk to an AI are detected and their edits held, but the document is still processed in the browser (planning edits, building the output file). A hostile page could change what it exports. The server judges every edit it is shown, not what the browser finally writes.
6. **The agent is hosted.** Text sent to the 🤖 button, to "let the agent choose a tool" and to the "why it was stopped" explanation goes to Groq (the key is held by the server and never sent to the page or to a local model). The safety check and all decisions stay on this computer. Do not give the agent anything you would not send to Groq.
7. **Local models run unauthenticated on localhost** (`wildguard_local.py` port 8000, Llama Guard port 8002, and a local Llama on 8001 if you use one). Any program on this Mac can call them.
8. **Rotate any API key that has appeared in a screenshot or a chat.** The key in `.env` is only read by the server and is no longer served to the browser (it used to be, at `/.env`, before this version).

## Checklist before showing or sharing

- [ ] Change the sample PINs (`python3 scripts/add_user.py ravi "Ravi Kumar" reviewer`, same for admin).
- [ ] Replace `policy_data/sanctions.csv` and `consent_lists.csv` (they are made-up).
- [ ] Rotate the LLM key and keep it only in `.env` (not in chat, screenshots or the zip).
- [ ] Share with `sh scripts/make_release_zip.sh` (leaves out `.env`, `records/`, databases, caches, real users).
- [ ] `sh scripts/list_project_copies.sh` shows old copies of the project; delete the ones you no longer want yourself.
