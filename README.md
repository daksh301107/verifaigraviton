# VerifAI console

Agentic AI guardrail validator: every proposed action is checked for whether it is safe, authorized,
explainable and reversible before it may run. Pipeline: Input → Action Analyzer → WildGuard →
Weighted Risk Engine → Authorization (min(AAL, ACL)) → Decision Engine → Trajectory / Explanation →
Signed Evidence Ledger.

## Run

```
pip install scikit-learn joblib sqlalchemy      # the models and the reversal graph
# Node.js is required: the decision engine runs in it, inside the server (https://nodejs.org)
cp .env.example .env                            # optional: an LLM key, or point it at a local model (llama_local.py)
python3 server.py                               # http://localhost:8765  (VERIFAI_PORT to change)
```
**Or one command for the whole demo:** `./start.sh` starts whatever is not running (WildGuard and the server), reuses what already is, and prints a ready-to-demo report. `./start.sh check` only checks, `./start.sh stop` stops what it started. `--llamaguard` also starts Llama Guard (the second safety model, port 8002; first start downloads about 0.9 GB). `--llama` also starts a local Llama agent (port 8001, about 4.5 GB) for use instead of Groq.
Open `http://localhost:8765`. Opening `index.html` directly no longer works: the decisions are made by the server.

**Sign-in page.** The first page has two doors: **User** (requesters such as `asha`, and reviewers such as `ravi`) and **Admin** (`meena`). Each door refuses the other kind of account. "Continue as guest" lets someone check requests but not approve them or change settings. A page reload keeps you signed in; closing the tab signs you out. Demo accounts (sample PINs, change them): `asha` 1111, `ravi` 2222, `meena` 3333. See `policy_data/README.md`.

## Which model does which job
* **Safety check:** **WildGuard 7B** (default) or **Llama Guard 3**, both on this computer. An admin switches between them in **Settings → Models**; the change is recorded in the ledger. Llama Guard is **not yet measured** (WildGuard is).
* **Agent** (the 🤖 button, "let the agent choose a tool", the "why it was stopped" explanation): **Groq-hosted `gpt-oss-20b`**. Its key stays on the server. **The text you give the agent is sent to Groq**; the safety check and every decision stay on this computer. (A local Llama can be used instead: see `AGENT_INTEGRATION.md`.)

## Where things are enforced (the short version; `SECURITY.md` has the full picture)

* **The verdict is made on the server** (`server.py` + `engine_worker.js`, which runs `engine.js`). The page sends what the person typed and draws what the server decided, so editing the page cannot change a verdict.
* **If a safety check does not run** (WildGuard or a model times out), a non-trivial action is held for a person instead of being allowed.
* **A held request goes to the person who made it first:** they can approve it, reject it, or press **Ask a reviewer**. A request sent to a reviewer waits in the **Reviews** tab (a few sample requests are there for demos); a reviewer decides it there, never on their own request, and the requester sees the answer. Settings and policy limits need an admin. Everything is recorded under a name ("approved by the requester" or "approved by the reviewer").
* **The ledger is written by the server**, chained, signed (HMAC) and checkpointed outside the project folder (`~/.verifai/`). Personal identifiers are masked before anything is saved.
* **An agent's answer or tool call is released by the server**, only after the guardrail allows it (or a reviewer approves). The old ungated agent endpoints are gone. The tool-call API (no tab in the page) shows structured tool calls running in a sandbox only when allowed.
* **Only the web page is served** (not `.env`, records, models or source), and only to this machine's own page.

## What is real and what is simulated

| Part | Status |
|---|---|
| Analyzer, risk engine, authorization, decision engine, trajectory, ledger | Real, deterministic code (`engine.js`) run by the server. Same input → same output. |
| Hard policy | 28 rules: patterns, a policy model for reworded requests, sanctions / consent lookups (**sample data**), a text normaliser against disguised input. See `HARD_POLICY.md`. |
| Reversal cost | Graph traversal over foreign keys of a **demo** SQLite database. The "heals on restore" flags are set by hand. |
| Intent / impact | Trained classifier (TF-IDF + logistic regression), blended with a heuristic. Its "sureness" is a heuristic, not a probability: see `EVALUATION.md`. |
| Safety model | WildGuard when `WILDGUARD_URL` is set (`python3 wildguard_local.py`), or Llama Guard (`./start.sh --llamaguard`). Without either a keyword stand-in is used and labelled; if a chosen model is down, requests are held for a person. |
| LLM agent | Groq `gpt-oss-20b` by default (hosted), or a local Llama 3.1 8B (`llama_local.py`). The answer is shown only if the guardrail releases it. |
| Tools | Seven sandbox tools on a SQLite database. Nothing real is touched. |

## Tests

```
node tests/run_tests.js              # 211 engine checks, incl. every hard rule
node tests/run_adversarial.js        # disguised violations, with and without the normaliser
python3 tests/test_server.py         # 66 checks of the server: auth, fail-closed, review rules, ledger tampering, tools, documents, saving
python3 tests/ui_smoke.py            # 28 checks in a real Chrome (pip install playwright): block, approve, sign-in, settings, document, tool, ledger
python3 tests/eval_large.py all      # 170 tool + 180 verdict requests written by another LLM (use --set 2 for the fresh set); about 18 minutes
python3 tests/measure_wildguard.py   # what each AI component adds (needs the server and WildGuard running; about 6 minutes, then cached)
python3 tests/test_policy_model.py   # policy model on paraphrases it never trained on
python3 tests/test_impact_model.py   # impact model
python3 tests/run_external_eval.py their.csv   # score prompts written by people who did not build it
```

## Files
`index.html style.css app.js` — the page · `engine.js docs.js` — the decision engine and document edits ·
`engine_worker.js` — runs the engine inside the server · `server.py` — the API · `vf_ledger.py vf_auth.py vf_engine.py vf_tools.py` — signed ledger, people and sessions, engine manager, sandbox tools ·
`policy_data/` — users and lookup lists · `models/` — trained models · `training/` — data generation and training ·
`vendor/` — PDF, OCR and PDF-writer libraries · `reversal_cost_engine/` — dependency-graph engine ·
`wildguard_local.py llama_local.py` — local models on a Mac · `scripts/` — users, release zip, old-copy finder ·
`SECURITY.md EVALUATION.md HARD_POLICY.md LEDGER_SCHEMA.md AGENT_INTEGRATION.md DESIGN_DECISIONS.md` — the documents.
