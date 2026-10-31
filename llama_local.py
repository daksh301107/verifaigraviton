#!/usr/bin/env python3
"""Runs Llama 3.1 8B Instruct on a Mac (Apple silicon) with MLX and serves it on an OpenAI-style
/v1/chat/completions URL, so server.py can use it as the agent's LLM with no API key and no internet.

    python3 llama_local.py                                   # port 8001
    LLAMA_MODEL=/path/to/model python3 llama_local.py

The first run downloads the 4-bit model (about 4.5 GB) from Hugging Face. Then in .env set:
    VERIFAI_LLM_BASE_URL=http://127.0.0.1:8001/v1/chat/completions
    VERIFAI_LLM_MODEL=llama-3.1-8b-instruct
The same file serves a Llama Guard build as the second safety model (LLAMA_MODEL=mlx-community/Llama-Guard-3-1B-chat-mlx-4bit LLAMA_PORT=8002;
./start.sh --llamaguard does this). Requests are handled one at a time (a lock), like wildguard_local.py, so WildGuard and Llama can run side
by side on a 16 GB MacBook (about 4.5 GB each). Binds to 127.0.0.1 only.
"""
import json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = os.environ.get('LLAMA_MODEL', 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit')
PORT = int(os.environ.get('LLAMA_PORT', '8001'))
MAX_NEW = 512
_lock = threading.Lock()
_state = {}


def _load():
    from mlx_lm import load
    t = time.time()
    _state['model'], _state['tok'] = load(os.path.expanduser(MODEL))
    print(f'{MODEL} loaded in {time.time() - t:.1f}s', flush=True)


def _template(tok, messages):
    """Llama and most chat models take plain-string messages; some builds (Llama Guard 3) expect content as a list of text parts."""
    try:
        return tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    except Exception:
        parts = [{'role': m['role'], 'content': [{'type': 'text', 'text': m['content']}]} for m in messages]
        return tok.apply_chat_template(parts, tokenize=False, add_generation_prompt=True)


def _chat(messages, max_tokens, temperature):
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    tok = _state['tok']
    prompt = _template(tok, messages)
    with _lock:
        return generate(_state['model'], tok, prompt=prompt, max_tokens=max_tokens,
                        sampler=make_sampler(temp=max(0.0, float(temperature))), verbose=False).strip()


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        return self._json(200, {'ok': 'model' in _state, 'model': MODEL})

    def do_POST(self):
        if not self.path.startswith('/v1/chat/completions'):
            return self._json(404, {'error': 'not found'})
        try:
            d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            msgs = [{'role': m['role'], 'content': str(m['content'])} for m in d['messages']]
            text = _chat(msgs, min(int(d.get('max_tokens', 200)), MAX_NEW), d.get('temperature', 0.3))
            return self._json(200, {'model': 'llama-3.1-8b-instruct', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}]})
        except Exception as e:
            return self._json(500, {'error': str(e)})

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    _load()
    _chat([{'role': 'user', 'content': 'Say hi.'}], 4, 0)
    print(f'Llama 3.1 8B Instruct ready on http://127.0.0.1:{PORT}/v1/chat/completions', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
