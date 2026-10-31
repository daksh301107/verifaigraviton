#!/usr/bin/env python3
"""Runs allenai/wildguard on a Mac (Apple silicon) with MLX and serves it on an OpenAI-style
/v1/completions URL that server.py can call (WILDGUARD_URL).

    python3 wildguard_local.py                       # uses ~/wildguard-4bit on port 8000
    WILDGUARD_MODEL_PATH=/path/to/model python3 wildguard_local.py

Why not `mlx_lm.server`? On a 16 GB MacBook it ran out of GPU memory while batching prompts; one request
at a time through mlx_lm.generate needs about 4.5 GB. Requests are handled one at a time (a lock).
Binds to 127.0.0.1 only. One-time model preparation: see WILDGUARD_SETUP.md ("On a Mac").
"""
import json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_PATH = os.path.expanduser(os.environ.get('WILDGUARD_MODEL_PATH', '~/wildguard-4bit'))
PORT = int(os.environ.get('WILDGUARD_PORT', '8000'))
_lock = threading.Lock()
_state = {}


def _load():
    from mlx_lm import load
    t = time.time()
    _state['model'], _state['tok'] = load(MODEL_PATH)
    print(f'model loaded from {MODEL_PATH} in {time.time() - t:.1f}s', flush=True)


def _generate(prompt, max_tokens):
    from mlx_lm import generate
    with _lock:
        return generate(_state['model'], _state['tok'], prompt=prompt, max_tokens=max_tokens, verbose=False)


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        return self._json(200, {'ok': 'model' in _state, 'model': MODEL_PATH})

    def do_POST(self):
        if not self.path.startswith('/v1/completions'):
            return self._json(404, {'error': 'not found'})
        try:
            d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            text = _generate(str(d['prompt']), min(int(d.get('max_tokens', 32)), 64))
            return self._json(200, {'choices': [{'text': text}]})
        except Exception as e:
            return self._json(500, {'error': str(e)})

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    if not os.path.isdir(MODEL_PATH):
        raise SystemExit(f'Model folder not found: {MODEL_PATH}. See WILDGUARD_SETUP.md ("On a Mac").')
    _load()
    _generate('<|user|>\n[INST] warm-up\n\nHuman user:\nhi\n\nAI assistant:\n\n\n---\n\nAnswers: [/INST]\n<|assistant|>\n', 4)
    print(f'WildGuard ready on http://127.0.0.1:{PORT}/v1/completions', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
