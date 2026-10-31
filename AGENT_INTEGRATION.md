# Agent integration — a real LLM gated by the guardrail

> **Current default: Groq (hosted `gpt-oss-20b`).** Using the 🤖 button sends your text to Groq; the safety check and the decision stay on this computer. A local Llama 3.1 8B can be used instead (section "Using Llama 3.1 8B on your own Mac" below): comment out nothing, just set the two `VERIFAI_LLM_*` lines in `.env` to the local address.

## Flow

1. You type input in the main box and click **🤖** instead of →.
2. `POST /api/agent/run` (server side) sends your exact input to an LLM, which drafts
   the response/output it would give, while the same input is judged. The draft
   never leaves the server unless the guardrail allows it. The old
   `/api/agent/output` and `/api/agent/feedback`, which returned drafts without that
   check, have been removed.
3. At the same time, that same input is parsed into an action and run
   through the **existing, unchanged guardrail pipeline** — Action Analyzer,
   WildGuard, Weighted Risk Engine, Authorization, Decision Engine — exactly
   as any typed prompt already is. The guardrail doesn't know or care that an
   LLM is involved.
4. The guardrail's verdict decides whether you ever see the LLM's draft:
   - **ALLOW** → the drafted output is shown.
   - **REVIEW / BLOCK** → the draft is held (REVIEW: released only after a signed-in
     reviewer, other than you, approves) or thrown away (BLOCK) and never shown. Instead,
     the server asks the LLM to explain, in its own words,
     what was asked, why the guardrail stopped it, and what would need to
     change — counterfactual feedback instead of the answer.

This is the honest story for judges: the guardrail is a gate in front of
whatever the LLM produces, not a step the LLM can reason its way around —
the human never sees ungated model output.

## Using Llama 3.1 8B Instruct on your own Mac (no key, no internet)

`llama_local.py` runs Meta's Llama 3.1 8B Instruct (4-bit, about 4.5 GB) with Apple's MLX and serves it the way a hosted provider would, so the agent LLM works offline and nothing you type leaves the machine.

1. In a new Terminal tab, in this folder (the first run downloads the model, about 4.5 GB):
   ```
   python3 llama_local.py
   ```
   Wait for `Llama 3.1 8B Instruct ready on http://127.0.0.1:8001/...`. If Hugging Face asks you to sign in or accept Meta's licence for the model, do that once with `huggingface-cli login`.
2. In `.env` set these two lines (comment out the Groq ones if you want Llama only), then restart `python3 server.py`:
   ```
   VERIFAI_LLM_BASE_URL=http://127.0.0.1:8001/v1/chat/completions
   VERIFAI_LLM_MODEL=llama-3.1-8b-instruct
   ```
   No API key is needed for a localhost address. The System panel then shows `LLM agent: Ready · llama-3.1-8b-instruct (runs on this Mac)`.
3. Use the 🤖 button as before. The guardrail still decides whether the draft is shown.

Notes:
- **Memory.** WildGuard (about 4.5 GB) and Llama (about 4.5 GB) can run together on a 16 GB MacBook, but close heavy apps first. Both handle one request at a time.
- **Speed.** About 30 tokens a second on Apple silicon, so a short answer takes a few seconds. The server waits up to 90 seconds for a local model.
- **Quality.** An 8B model writes shorter, plainer answers than the hosted `gpt-oss-20b`. The guardrail does not depend on the LLM, so verdicts are the same either way.
- To go back to Groq, restore the two `.env` lines and restart the server.

## Getting a free, long-lived API key

Default provider is **Groq** (default model `openai/gpt-oss-20b`; Groq retires models from time to time, so if you see `model_not_found`, list the current ones with `curl -H "Authorization: Bearer $KEY" https://api.groq.com/openai/v1/models` and set `VERIFAI_LLM_MODEL`) — free tier, no expiry, fast, OpenAI-compatible.

1. https://console.groq.com/keys — sign in (free, no card).
2. Create a key, copy it.
3. Put it in `.env` (copy `.env.example`), or export it before starting the server:
   ```
   export VERIFAI_LLM_API_KEY=gsk_your_key_here
   python3 server.py
   ```

To use a different OpenAI-compatible provider (OpenAI, OpenRouter, etc.):
```
export VERIFAI_LLM_BASE_URL=https://api.openai.com/v1/chat/completions
export VERIFAI_LLM_MODEL=gpt-4o-mini
export VERIFAI_LLM_API_KEY=sk-...
python3 server.py
```

Without a key, `/api/agent/*` returns 503 and the 🤖 card says so — nothing
else in the app is affected.

## Verify

```
curl http://localhost:8765/api/agent/status
```
`{"ok": true, ...}` means the key is picked up. Then in the UI, click 🤖 on
something clearly risky (e.g. *"wire $50,000 to this new vendor account"*)
and something clearly safe (e.g. *"what's our refund policy?"*) to see both
paths: released output vs. withheld output + feedback.


## Output screening (added)
The guardrail's verdict covers the *request*. The model's *draft* is screened too before anyone sees it: injected
instructions, harmful content and leaked credentials withhold it ("withheld (output screening)"), and personal data in an
otherwise fine answer is masked (`[email masked]`, `[Aadhaar masked]`, …). The screening uses keyword rules, the same
class of check as the prompt-side WildGuard stand-in. The 🤖 button is on both the main input and the follow-up box.

If Groq answers `403 error code: 1010`, the request was blocked by its edge for looking like a bare script; `server.py` sends a User-Agent header for that reason.
