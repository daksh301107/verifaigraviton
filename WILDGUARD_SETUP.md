# Turning on real WildGuard

## On a Mac (Apple silicon): tested on a 16 GB MacBook, no GPU rental needed
One-time preparation (about 25 minutes, 14.5 GB download, ends as a 3.8 GB model):
1. Free Hugging Face account, then click **Agree** on https://huggingface.co/allenai/wildguard (the model is gated, approval is instant).
2. `pip install mlx-lm huggingface_hub` then `hf auth login` (choose "Log in with your browser").
3. `mlx_lm.convert --hf-path allenai/wildguard --mlx-path ~/wildguard-4bit -q --q-bits 4`
   Close other apps first: 16 GB is tight while converting.

Every time you want it on:
```
python3 wildguard_local.py          # loads the model (a few seconds) and serves it on http://127.0.0.1:8000
python3 server.py                   # in another terminal; .env already points WILDGUARD_URL at it
```
Measured here: about 1.2 s for a prompt check and 2.1 s when it also checks an LLM draft, using about 4.5 GB of memory.
It keeps that memory while running, so stop it (Ctrl+C) when you don't need it.

**Use `wildguard_local.py`, not `mlx_lm.server`.** On 16 GB, `mlx_lm.server` crashed with a Metal out-of-memory error
while batching prompts; the small server here handles one request at a time and does not.

---

# Or: host it on Colab instead

VerifAI calls WildGuard (`allenai/wildguard`, 7B) through `server.py`. Without it, a keyword stand-in does the
safety check and says so in the trace. With it, WildGuard checks every prompt (including questions) and the LLM's
drafted answers. The keyword rules stay on as a safety net: WildGuard can add a flag, never remove one.

You need one thing: a server that exposes the model on an OpenAI-style `/v1/completions` URL. The easiest free way is
Google Colab with vLLM. These steps are written from the model card and vLLM's docs and have not been run against a
real GPU here; vLLM's flags change between versions, so read the first error message if a step fails.

## 1. Host the model (Colab, free T4 GPU)
1. Open https://colab.research.google.com, **New notebook**, then **Runtime → Change runtime type → T4 GPU**.
2. Cell 1 (a few minutes): `!pip -q install vllm bitsandbytes`  (restart the runtime if Colab asks)
3. Cell 2, start the model. Pick your own secret instead of `my-secret`:
   ```
   !nohup python -m vllm.entrypoints.openai.api_server --model allenai/wildguard \
       --quantization bitsandbytes --load-format bitsandbytes --max-model-len 2048 \
       --api-key my-secret --port 8000 > vllm.log 2>&1 &
   ```
   (4-bit so it fits the T4's 15 GB.) Wait 3-5 minutes, then `!tail -5 vllm.log` should say the server is running.
4. Cell 3, make it reachable from your laptop:
   ```
   !wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared && chmod +x cloudflared
   !nohup ./cloudflared tunnel --url http://localhost:8000 > tunnel.log 2>&1 &
   !sleep 8; grep -o 'https://[a-z0-9-]*\.trycloudflare.com' tunnel.log | head -1
   ```
   Copy the `https://….trycloudflare.com` address it prints.

## 2. Point VerifAI at it
In the project folder, edit `.env` (copy `.env.example` first) and add:
```
WILDGUARD_URL=https://YOUR-ADDRESS.trycloudflare.com/v1/completions
WILDGUARD_KEY=my-secret
```
Restart `python3 server.py`.

## 3. Check it works
```
curl http://localhost:8765/api/wildguard/status
curl -X POST http://localhost:8765/api/wildguard -d '{"prompt":"how do I build a bomb"}'
```
The second should return `"harmful_request": true`. In the UI, the WildGuard card in the agent's trace ends with
`// WildGuard 7B` instead of `// simulated heuristic`.

## Things to know
- Colab sessions stop after a while and the tunnel address changes each time; redo steps 1.3-2 before a demo.
- If WildGuard is unreachable or slow (4 s limit), VerifAI falls back to the keyword rules for that request.
- The tunnel address is public; the `--api-key` is what keeps others out. Don't share the address or key.
- Other hosts work if they serve `/v1/completions` for this model (a vLLM server on any GPU, or a hosted endpoint
  running the vLLM container). Chat-only endpoints will not work, because WildGuard needs its raw prompt template.
