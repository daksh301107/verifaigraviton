# Technology stack, stage by stage

How a request moves through VerifAI and what each step is built with. Matches the two slides in `VerifAI_Architecture_Slides.pptx` (rebuild them with `python3 scripts/build_slides.py`).

## The processes that run

| Process | Port | What it is | Started by |
|---|---|---|---|
| **VerifAI server** | 8765 | Python 3.12 `http.server` (standard library only): serves the page, the API, sessions, sign-in, the signed ledger, the tool gateway | `python3 server.py` |
| **Decision engine** | stdin / stdout | `engine_worker.js`: Node.js loads `engine.js` and `docs.js`, one JSON line in, one out | started by the server |
| **WildGuard** | 8000 | `allenai/wildguard` 7B, 4-bit, Apple MLX, one request at a time | `python3 wildguard_local.py` |
| **Llama Guard 3** (optional second safety model) | 8002 | 1B 4-bit by default (about 0.9 GB), Apple MLX, same server file as Llama | `./start.sh --llamaguard` |
| **Llama 3.1 8B Instruct** (optional local agent) | 8001 | 4-bit, Apple MLX; used only if `.env` points the agent at it | `./start.sh --llama` |
| **Groq `gpt-oss-20b`** (the agent, hosted) | — | called by the server with the key in `.env`; the only part that sends text off this computer | — |

Python packages: `scikit-learn`, `joblib`, `numpy`, `sqlalchemy`; `mlx-lm` for the two local models. Node.js 18 or newer. Browser libraries are vendored in the project (Chart.js 4.4.1, JSZip 3.10, pdf.js 3.11, jsPDF 2.5, Tesseract.js 5), so it works offline.

## The pipeline

### 1. Input: the person and the page
| | |
|---|---|
| **Built with** | Browser page in HTML, CSS and plain JavaScript (no framework). Voice in and out through the Web Speech API. File readers: pdf.js (PDF text), JSZip (docx), Tesseract.js (OCR for scans and photos). |
| **Takes** | Typed or spoken prompts; txt, md, csv, json, docx, pdf, png, jpg; structured tool calls (API only). |
| **Why** | No build step, nothing to install for the user, and every library works offline. |
| **If it fails** | If the server does not answer, the page says nothing was checked or run. It never decides on its own. |

### 2. Analyze: understanding what is being asked
| | |
|---|---|
| **Built with** | `engine.js` running in the Node worker on the server. The Action Analyzer turns text into an action (type, amount, count, table, backup, scope) with patterns plus typo repair (edit-distance against a vocabulary). A **text normaliser** adds the plain reading of disguised text (look-alike letters, zero-width characters, leetspeak, spaced-out letters, base64, hex, ROT13, reversed text, a small Hindi / Hinglish lexicon). Tool calls skip guessing: `fromStructured` reads the arguments directly. |
| **Why** | Deterministic: the same input always gives the same action. Normalising first means the rules see what is hidden, not just the disguise. |
| **If it fails** | The engine is down: the API answers 503 and the request is refused, never waved through. A request not understood is not guessed: the person is asked to rephrase. |
| **Tested** | `node tests/run_tests.js` (195 checks), `node tests/run_adversarial.js` (disguised attacks). |

### 3. Safety: is it harmful or forbidden?
| | |
|---|---|
| **Built with** | **WildGuard 7B** or **Llama Guard 3** (safety classifiers for prompts and answers; an admin picks one in Settings), 4-bit through Apple MLX on the laptop. **27 hard policy rules** (patterns, in `engine.js`). A **policy model** (TF-IDF word and character n-grams + logistic regression, about 8,100 examples, 21 rules) that recognises reworded violations. **Lookups** against `policy_data/sanctions.csv` and `consent_lists.csv`. |
| **Why** | Three layers fail differently: patterns are exact but narrow, the model generalises but can be wrong, WildGuard judges harm in general. A hard rule always wins and cannot be argued with. |
| **If it fails** | If WildGuard or a model does not answer, the server lists it as `degraded` and any non-read-only action is held for a person. A draft that WildGuard cannot check is withheld. |
| **Tested** | `tests/hard_policy_cases.json`, `tests/hard_policy_paraphrases.json` + `python3 tests/test_policy_model.py`, `python3 tests/test_server.py` (fail-closed checks). |

### 4. Risk: how bad if it goes wrong?
| | |
|---|---|
| **Built with** | **Reversal cost**: a dependency graph read from the foreign keys of a SQLite database through SQLAlchemy, then traversed; cost is how hard a restore is plus side effects a restore cannot heal. **Impact model**: TF-IDF + logistic regression (LOW / MID / HIGH), blended with a heuristic and ignored below 60% sureness. **Policy score** from the soft rules. Combined by weights into a 0 to 10 score. |
| **Why** | Reversibility is the heart of the project: a deleted record with no backup and many dependents is worse than a typo fix. The model only nudges the heuristic, so the number stays explainable. |
| **If it fails** | A model that does not answer is treated like any failed check: the action is held for a person and the reason is shown. |
| **Tested** | `python3 tests/test_impact_model.py`; accuracy 92.8% and 96.7% on two hand-written sets, calibration gap about 5.5 points (`EVALUATION.md`). |

### 5. Authorize: is the agent allowed this much?
| | |
|---|---|
| **Built with** | Two numbers from 0 to 10. **ACL**: how capable the agent is for this action (understanding + an undo path + scope). **AAL**: autonomy it has earned in this session (starts at 6, grows with clean actions, shrinks with blocks and reviews, floor 1.5). The action needs `min(ACL, AAL)` to reach its required level. History is kept **on the server** per session. |
| **Why** | Trust is earned and lost, and a low-risk action can still be too much for an agent that has just been blocked twice. Keeping the history on the server stops the page resetting it. |
| **If it fails** | A new session starts at 6; nothing can push autonomy below 1.5. |

### 6. Decide
| | |
|---|---|
| **Built with** | Thresholds (review from, block from) on the weighted score; **trajectory risk** over the session (repeated small payments, structuring); a hard rule short-circuits to BLOCK before scoring. Output: ALLOW, REVIEW or BLOCK, with the stage that decided. |
| **Why** | Single actions can look fine while the pattern is not (five small transfers that add up). |
| **Settings** | Thresholds and policy limits can only be changed by a signed-in admin, and each change is a ledger row. |

### 7. Escalate: a person decides, and the agent explains
| | |
|---|---|
| **Built with** | Sign-in by PIN (PBKDF2-SHA256, lockout after 5 wrong tries). A held action needs a signed-in **reviewer** who is **not the requester**. An agent's held answer, or a held tool call, is released or run by the server only after approval. The **agent model** (Groq `gpt-oss-20b` by default) writes the counterfactual: what was asked, why it was stopped, what would change. Output is screened before it is shown. |
| **Why** | Accountability: every approval has a name, and nobody approves their own request. |
| **If it fails** | No reviewer signed in: the action stays held. |
| **Tested** | `python3 tests/test_server.py` (guest, wrong PIN, requester role, maker ≠ checker, double approval). |

### 8. Ledger: the record
| | |
|---|---|
| **Built with** | Server-written JSON lines. Each row: SHA-256 over the previous hash plus its own fields, an **HMAC-SHA256 signature** with a key in `~/.verifai/`, and a checkpoint line in `~/.verifai/anchors.log`. Personal identifiers are masked before writing. `GET /api/ledger/verify` re-checks everything. Per-action HTML and JSON reports and edited documents are saved under `records/`. |
| **Why** | Tamper-evident history that can be replayed and shown to an auditor. |
| **If it fails** | Editing a row, or cutting entries off the end, makes verify name the first bad row. **Limit:** whoever controls this Mac account can read the key; anchoring the latest hash somewhere else (`VERIFAI_ANCHOR_URL`) closes that gap. |

## Cross-cutting

| Concern | Built with |
|---|---|
| **Tool gateway** | `vf_tools.py`: seven tools over a SQLite sandbox (`records/sandbox.db`). Arguments are validated, then judged as structured data, then run only if allowed. Nothing real is touched. |
| **Web security** | Server listens on 127.0.0.1 only. Only `index.html`, the scripts, `style.css` and `vendor/` are served. `Host` and `Origin` are checked, and every write needs the session header. |
| **Documents** | `docs.js` plans edits in the browser; each edit is judged by the server. Lines in the document that talk to an AI are detected and their edits held. Undo and "change it back" work on the current version. |
| **Training** | `training/`: data generation (templates + an LLM), `train_impact.py`, `train_policy.py`, `calibration.py`. Models are scikit-learn pipelines saved with joblib in `models/`. |

## Honest limits
Real: the engine, rules, models, ledger, sign-in, approvals, tool gateway. Simulated or sample: the reversal graph (a demo database with hand-set "heals on restore" flags), the sanctions and consent lists, the demo users and PINs, the tools (sandbox only). See `EVALUATION.md` for what was measured and `SECURITY.md` for what is not protected.
