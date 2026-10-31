# Backend integration — what changed

The UI was already a complete, self-contained simulation of the pipeline (`engine.js`), with one honest gap it flagged in its own comments: *"WildGuard (7B) is NOT called; a keyword heuristic stands in for it."* Real WildGuard inference needs model hosting this environment doesn't have, so that part is unchanged. What *is* now real: the **intent & impact classifier** trained this session (TF-IDF + logistic regression, Dataset B, 500 train / 100 test rows — 100% intent-match and 93% impact accuracy on the 100 held-out rows of that dataset; **these figures describe that synthetic set only**. On free-text prompts the model is noisy (it rates "what is 5+2" HIGH), which is why it is gated: see `DESIGN_DECISIONS.md`).

## What was added

- **`models/`** — the three trained `.joblib` files (vectorizer, intent-match classifier, impact classifier).
- **`server.py`** — new `POST /api/intent-impact` endpoint. Loads the models once, runs real inference, returns `{intent_match, impact, confidence}`. New `GET /api/intent-impact/status` for a quick health check. If `joblib`/`scikit-learn` aren't installed, or the model files are missing, the endpoint reports itself unavailable — it never crashes the server.
- **`engine.js`** — new `enrich(action)` async function: POSTs to `/api/intent-impact` with a 1.2s timeout, attaches the result to the action as `a.backend`. `impact()` now blends the model's prediction with the existing heuristic (50/50) when the backend answered, and falls back to the pure heuristic, unchanged, when it didn't. `policy()` adds a soft-rule point cost when the model detects a declared-intent/actual-action mismatch.
- **`app.js`** — `runPrompt()` now awaits `V.enrich(action)` before evaluating. This is the *only* call site wired up (not the document-editing path, which evaluates many ops per document — routing each through a network call risked slowing that flow down for a benefit that path doesn't need as much).

## Run it

```
pip install scikit-learn joblib   # if not already installed
python3 server.py
# open http://localhost:8765
```

Works without this step too — the UI falls back to the original heuristic automatically (tested: killing the server mid-session and submitting an action still produces a correct verdict, just with `// no backend reached, heuristic only` shown in the trace instead of the model's prediction).

## What to tell judges

The pipeline is explainable by design — the trace already shows which signal produced which number. With this change, it also shows **which signal was a trained model and which was a rule**, live, per action: look for `model says HIGH (84% conf)` versus `heuristic only` in the Weighted Risk Engine stage's code block.


## Later changes
- The model is **not consulted for read-only requests**, predictions under 60% confidence are **ignored**, and the model can lower a risk estimate by **at most 1.0**.
- `intent_match` only counts when a real declared intent is supplied (the UI sends none), so it no longer adds a policy penalty.
- The trace says why the model was or wasn't used (`model unsure (37% < 60%), ignored`, `read-only request, model not consulted`).
- New: `/api/schema` (dependency graph + traversals from the SQLite demo database), `/api/ledger` (for Verify chain).

## Impact model v2
The impact model was retrained on 6,320 generated examples with typo-robust features and amount-size features (`training/`, `impact_text.py`). `/api/intent-impact` now returns the probability of each class, `/api/model-info` returns the measured accuracy, and the app's **Model confidence** button shows both. See `training/README.md` for the numbers and their limits.
