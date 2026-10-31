#!/usr/bin/env python3
"""VerifAI local server.
Serves the app and saves every committed action into ./records next to this file:
  records/ledger.jsonl            one line per decision (append-only, hash-chained)
  records/actions/<n>_<verdict>_<slug>.json   full decision data
  records/actions/<n>_<verdict>_<slug>.html   readable report
  records/outputs/<n>_<name>      the edited output file, for document actions

Also serves /api/intent-impact — real inference from the trained intent/impact
classifier (TF-IDF + logistic regression, trained on Dataset B), replacing the
client-side keyword heuristic for the single-action prompt path. If the models
or scikit-learn/joblib aren't available, the endpoint reports itself as
unavailable and engine.js silently keeps using the deterministic heuristic —
the app works identically either way, this just upgrades the live signal when
the backend is present.

Also serves /api/agent/* — a real LLM in the loop. Flow: the human's input
goes to the LLM, which drafts the output it would give; at the same time the
*same* input is parsed into an action and run through the existing, unchanged
guardrail pipeline (WildGuard, Weighted Risk Engine, Authorization, Decision
Engine) exactly as any typed prompt is. The guardrail decides whether the
LLM's drafted output is released: ALLOW shows it, REVIEW/BLOCK withholds it
and shows counterfactual feedback instead (what was asked, why it was
stopped, what would need to change). The guardrail logic itself never
changes — the LLM sits in front of it (drafting) and the gate sits after
(releasing or withholding). Needs an API key: see AGENT_INTEGRATION.md.
Without one, /api/agent/* reports itself unavailable (503) and the UI's 🤖
button says so; everything else keeps working.

Binds to 127.0.0.1 only. Run:  python3 server.py   then open http://localhost:8765
"""
import base64, json, os, re, threading, time
import urllib.request, urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
REC = os.environ.get('VERIFAI_RECORDS') or os.path.join(ROOT, 'records')
MODELS = os.path.join(ROOT, 'models')
POLICY_DATA = os.path.join(ROOT, 'policy_data')
MAX = 60 * 1024 * 1024

# ---------------------------------------------------------------- intent/impact model (lazy, optional)
_model_state = {'tried': False, 'ok': False, 'vectorizer': None, 'clf_match': None, 'clf_impact': None, 'impact_v2': None, 'error': None}

# ---------------------------------------------------------------- agent LLM (optional, needs an API key)
# Free, long-lived keys: Groq (console.groq.com) is the default — generous free tier, fast, no
# expiry. Any OpenAI-compatible chat-completions endpoint works by changing the two env vars below.
def _load_dotenv():
    # Optional ./.env (KEY=VALUE per line) so the key doesn't need exporting in every terminal.
    p = os.path.join(ROOT, '.env')
    if not os.path.isfile(p):
        return
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
LLM_API_KEY = os.environ.get('VERIFAI_LLM_API_KEY', '') or os.environ.get('GROQ_API_KEY', '')
LLM_BASE_URL = os.environ.get('VERIFAI_LLM_BASE_URL', 'https://api.groq.com/openai/v1/chat/completions')
LLM_LOCAL = bool(re.match(r'https?://(127\.0\.0\.1|localhost|\[::1\])[:/]', LLM_BASE_URL))   # e.g. llama_local.py: no key, no internet
LLM_READY = bool(LLM_API_KEY) or LLM_LOCAL
LLM_MODEL = os.environ.get('VERIFAI_LLM_MODEL', 'llama-3.1-8b-instruct' if LLM_LOCAL else 'openai/gpt-oss-20b')   # Groq retired llama-3.1-8b-instant; models change, see AGENT_INTEGRATION.md


def _call_llm(system, user, max_tokens=200, temperature=0.3):
    """POSTs an OpenAI-compatible chat-completion request. Returns (text, error) — exactly
    one of the two is set. Never raises: any failure (no key, bad key, network, non-2xx,
    unexpected shape) comes back as an error string so the caller can fail soft."""
    if not LLM_READY:
        return None, 'no LLM API key set (export VERIFAI_LLM_API_KEY=... — see AGENT_INTEGRATION.md)'
    payload = {
        'model': LLM_MODEL,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'max_tokens': max_tokens,
        'temperature': temperature,
    }
    if 'gpt-oss' in LLM_MODEL:
        payload['reasoning_effort'] = 'low'      # otherwise hidden reasoning eats the token budget and the answer is cut off
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        LLM_BASE_URL, data=body, method='POST',
        headers={'Content-Type': 'application/json', 'User-Agent': 'VerifAI/1.0 (+local)', **({'Authorization': f'Bearer {LLM_API_KEY}'} if LLM_API_KEY and not LLM_LOCAL else {})},   # a key is never sent to a model on this machine
    )
    try:
        with urllib.request.urlopen(req, timeout=90 if LLM_LOCAL else 20) as resp:
            j = json.loads(resp.read().decode('utf-8'))
        return j['choices'][0]['message']['content'].strip(), None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8', errors='ignore')[:300]
        except Exception:
            detail = ''
        return None, f'LLM API {e.code}: {detail}'
    except Exception as e:  # DNS, timeout, bad JSON shape, anything — fail soft, never crash the server
        return None, f'LLM call failed: {e}'


# ---------------------------------------------------------------- WildGuard (optional, needs a hosted model)
# Point WILDGUARD_URL at any server that exposes an OpenAI-style /v1/completions endpoint for
# allenai/wildguard (vLLM, a Hugging Face Inference Endpoint, ...). See WILDGUARD_SETUP.md.
WG_URL = os.environ.get('WILDGUARD_URL', '')
WG_MODEL = os.environ.get('WILDGUARD_MODEL', 'allenai/wildguard')
WG_KEY = os.environ.get('WILDGUARD_KEY', '')
# Prompt template from the allenai/wildguard model card (raw completion, not chat).
WG_TEMPLATE = (
    "<|user|>\n[INST] You are a classifier for analyzing interactions between humans and LLMs. Given a request from a "
    "human user and a response from an LLM assistant, determine 1) whether the human user's request is harmful, 2) "
    "whether the AI assistant's response is a refusal, and 3) whether the AI assistant's response is harmful.\n\n"
    "Human user:\n{prompt}\n\nAI assistant:\n{response}\n\n---\n\nAnswers: [/INST]\n<|assistant|>\n"
)


def _wildguard(prompt, response=''):
    """Returns (result, error). result = {harmful_request, harmful_response, refusal, raw}. Never raises."""
    if not WG_URL:
        return None, 'WILDGUARD_URL is not set (see WILDGUARD_SETUP.md)'
    body = json.dumps({'model': WG_MODEL, 'prompt': WG_TEMPLATE.format(prompt=prompt[:4000], response=response[:4000]),
                       'max_tokens': 32 if response else 8, 'temperature': 0}).encode('utf-8')
    headers = {'Content-Type': 'application/json', 'User-Agent': 'VerifAI/1.0 (+local)'}
    if WG_KEY:
        headers['Authorization'] = f'Bearer {WG_KEY}'
    try:
        req = urllib.request.Request(WG_URL, data=body, method='POST', headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            j = json.loads(resp.read().decode('utf-8'))
        raw = (j['choices'][0].get('text') or j['choices'][0].get('message', {}).get('content') or '').strip()
    except Exception as e:
        return None, f'WildGuard call failed: {e}'
    low = raw.lower()

    def yn(label):
        m = re.search(label + r':\s*(yes|no)', low)
        return None if not m else m.group(1) == 'yes'
    hr = yn('harmful request')
    if hr is None:
        return None, f'unexpected WildGuard output: {raw[:120]!r}'
    return {'harmful_request': hr, 'harmful_response': yn('harmful response'), 'refusal': yn('response refusal'), 'raw': raw}, None

# ---------------------------------------------------------------- the safety role: WildGuard or Llama Guard
# Either one answers "is this harmful?" in the same shape, so the rest of the system does not care which is active.
# An admin picks it in Settings (recorded in the ledger). Llama Guard runs the same way as WildGuard: a local MLX server
# (python3 llama_local.py with LLAMA_MODEL set to a Llama Guard build, on port 8002; ./start.sh --llamaguard does it).
LG_URL = os.environ.get('LLAMAGUARD_URL', 'http://127.0.0.1:8002/v1/chat/completions')
LG_REPO = os.environ.get('LLAMAGUARD_REPO', 'mlx-community/Llama-Guard-3-1B-chat-mlx-4bit')
SAFETY = {'wildguard': {'name': 'WildGuard 7B', 'short': 'WildGuard'}, 'llamaguard': {'name': 'Llama Guard 3', 'short': 'Llama Guard'}}


def _llamaguard(prompt, response=''):
    """Returns (result, error) in WildGuard's shape. With a response, Llama Guard judges that reply to the request."""
    msgs = [{'role': 'user', 'content': prompt[:4000]}] + ([{'role': 'assistant', 'content': response[:4000]}] if response else [])
    body = json.dumps({'model': 'llama-guard', 'messages': msgs, 'max_tokens': 24, 'temperature': 0}).encode('utf-8')
    try:
        req = urllib.request.Request(LG_URL, data=body, method='POST', headers={'Content-Type': 'application/json', 'User-Agent': 'VerifAI/1.0 (+local)'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            j = json.loads(resp.read().decode('utf-8'))
        raw = j['choices'][0]['message']['content'].strip()
    except Exception as e:
        return None, f'Llama Guard call failed: {e}'
    low = raw.lower().lstrip()
    if not (low.startswith('safe') or low.startswith('unsafe')):
        return None, f'unexpected Llama Guard output: {raw[:120]!r}'
    unsafe = low.startswith('unsafe')
    cats = re.findall(r'S\d+', raw)
    return {'harmful_request': unsafe if not response else None, 'harmful_response': unsafe if response else None, 'refusal': None, 'raw': raw, 'categories': cats}, None


def _safety_id():
    return ((_state.get('models') or {}).get('safety')) or 'wildguard'


def _safety_configured():
    return bool(WG_URL) if _safety_id() == 'wildguard' else True


def _safety(prompt, response=''):
    sid = _safety_id()
    r, err = (_wildguard if sid == 'wildguard' else _llamaguard)(prompt, response)
    if r is not None:
        r['model'] = SAFETY[sid]['name']; r['short'] = SAFETY[sid]['short']
    return r, err


def _probe(url):
    """True when something answers at this address (the local model servers answer GET / with their status)."""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    try:
        with urllib.request.urlopen(f'{u.scheme}://{u.netloc}/', timeout=2) as r:
            return r.status == 200
    except Exception:
        return False



_model_lock = threading.Lock()


def _load_models():
    with _model_lock:
        return _load_models_locked()


def _load_models_locked():
    if _model_state['tried']:
        return _model_state['ok']
    _model_state['tried'] = True
    try:
        import joblib  # noqa: local import — optional dependency
        _model_state['vectorizer'] = joblib.load(os.path.join(MODELS, 'model_intent_impact_vectorizer.joblib'))
        _model_state['clf_match'] = joblib.load(os.path.join(MODELS, 'model_intent_match.joblib'))
        _model_state['clf_impact'] = joblib.load(os.path.join(MODELS, 'model_impact.joblib'))
        _model_state['ok'] = True
        # Impact model v2 (training/train_impact.py): one pipeline that reads the request text itself.
        # Used for the LOW/MID/HIGH impact when present; the original models remain as the fallback.
        try:
            _model_state['impact_v2'] = joblib.load(os.path.join(MODELS, 'model_impact_v2.joblib'))
        except Exception:
            _model_state['impact_v2'] = None
    except Exception as e:  # missing joblib, missing model files, version mismatch — any of these just disables the endpoint
        _model_state['error'] = str(e)
        _model_state['ok'] = False
    return _model_state['ok']


def _predict_intent_impact(declared_intent, action_text):
    if not _load_models():
        return None
    text = f"{declared_intent} || {action_text}"
    X = _model_state['vectorizer'].transform([text])
    match = bool(_model_state['clf_match'].predict(X)[0])
    v2 = _model_state['impact_v2']
    if v2 is not None:
        proba = v2.predict_proba([action_text or declared_intent])[0]
        k = int(proba.argmax())
        return {'intent_match': match, 'impact': str(v2.classes_[k]), 'confidence': round(float(proba[k]), 3), 'source': 'model-v2',
                'probs': {str(c): round(float(p), 3) for c, p in zip(v2.classes_, proba)}}
    impact = str(_model_state['clf_impact'].predict(X)[0])
    # confidence, when the classifier supports predict_proba (logistic regression does)
    conf = None
    try:
        proba = _model_state['clf_impact'].predict_proba(X)[0]
        conf = round(float(max(proba)), 3)
    except Exception:
        pass
    return {'intent_match': match, 'impact': impact, 'confidence': conf, 'source': 'model'}


# ---------------------------------------------------------------- policy check: rule-intent model + lookup lists
# The model (training/train_policy.py) says which hard-policy rule a request describes, whatever the wording.
# The lookups check names in the request against the sanctions and consent lists in policy_data/ (sample data
# until the compliance team provides the real lists). Both are advisory inputs to the browser's policy engine.
import csv
_policy_state = {'tried': False, 'model': None, 'error': None}
_policy_lock = threading.Lock()
_lookup_cache = {}


def _policy_model():
    with _policy_lock:
        if _policy_state['tried']:
            return _policy_state['model']
        _policy_state['tried'] = True
        try:
            import joblib  # noqa
            _policy_state['model'] = joblib.load(os.path.join(MODELS, 'model_policy_intent.joblib'))
        except Exception as e:
            _policy_state['error'] = str(e)
        return _policy_state['model']


def _read_lookup(name):
    """Rows of a policy_data CSV, re-read when the file changes. None when the file is missing or empty."""
    path = os.path.join(POLICY_DATA, name)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    c = _lookup_cache.get(name)
    if c and c[0] == mt:
        return c[1]
    with open(path, encoding='utf-8', newline='') as f:
        rows = [r for r in csv.DictReader(f) if any((v or '').strip() for v in r.values())]
    _lookup_cache[name] = (mt, rows or None)
    return rows or None


def _policy_check(text):
    t = (text or '').lower()
    out = {'model': None, 'lookups': {}}
    clf = _policy_model()
    if clf is not None:
        proba = clf.predict_proba([text or ' '])[0]
        classes = [str(c) for c in clf.classes_]
        ni = classes.index('none')
        viol = 1.0 - float(proba[ni])                     # chance the request breaks SOME rule
        rest = proba.copy(); rest[ni] = 0
        order = rest.argsort()[::-1][:3]                  # likeliest rules, "none" excluded
        top = [{'rule': classes[i], 'p': round(float(rest[i]), 3)} for i in order]
        out['model'] = {'rule': top[0]['rule'] if viol >= 0.2 else 'none', 'p': round(viol, 3), 'top': top}
    sanc = _read_lookup('sanctions.csv')
    if sanc is None:
        out['lookups']['sanctions'] = {'status': 'unavailable', 'hits': []}
    else:
        hits = [{'name': r['name'], 'reason': r.get('reason', '')} for r in sanc if r.get('name') and r['name'].strip().lower() in t]
        out['lookups']['sanctions'] = {'status': 'hit' if hits else 'clear', 'hits': hits, 'size': len(sanc)}
    cons = _read_lookup('consent_lists.csv')
    if cons is None:
        out['lookups']['consent'] = {'status': 'unavailable', 'hits': []}
    else:
        hits = [{'list': r['list_name'], 'status': r.get('status', '')} for r in cons if r.get('list_name') and r['list_name'].strip().lower() in t]
        bad = [h for h in hits if h['status'].strip().lower() == 'no_consent']
        out['lookups']['consent'] = {'status': 'hit' if bad else ('clear' if hits else 'not_mentioned'), 'hits': hits, 'size': len(cons)}
    return out


# ---------------------------------------------------------------- reversal-cost engine on a real database
# The dependency graph is discovered from the database's foreign keys (reversal_cost_engine/depgraph.py)
# and traversed with find_dependents(), instead of the hardcoded table in the browser. Needs sqlalchemy;
# without it the UI keeps its built-in copy of the demo graph.
_schema_state = {'tried': False, 'payload': None, 'error': None}
_schema_lock = threading.Lock()      # the warm-up thread and a first page request must not race


def _schema_payload():
    with _schema_lock:
        return _schema_payload_locked()


def _schema_payload_locked():
    if _schema_state['tried']:
        return _schema_state['payload']
    _schema_state['tried'] = True
    try:
        import sys
        sys.path.insert(0, os.path.join(ROOT, 'reversal_cost_engine'))
        from schema import build_demo_db
        from depgraph import get_dependency_graph, find_dependents, EDGE_HEALS_ON_RESTORE
        engine = build_demo_db(os.path.join(ROOT, 'reversal_cost_engine', 'demo.db'))
        graph = get_dependency_graph(engine)
        edges = [{'child': c, 'parent': f['parent_table'], 'heals': bool(EDGE_HEALS_ON_RESTORE.get((c, f['parent_table']), False))}
                 for c, fks in graph.items() for f in fks]
        trav = {}
        for t in graph:
            reached = {}
            for d in find_dependents(engine, graph, t, 1):
                r = reached.setdefault(d['table'], {'count': 0, 'depth': d['depth'], 'heals': d['heals_on_restore']})
                r['count'] += 1
                r['depth'] = min(r['depth'], d['depth'])
            trav[t] = reached
        _schema_state['payload'] = {'ok': True, 'source': 'database', 'tables': sorted(graph), 'edges': edges, 'traversals': trav}
    except Exception as e:
        _schema_state['error'] = str(e)
        _schema_state['payload'] = {'ok': False, 'error': str(e)}
    return _schema_state['payload']


def slug(s, n=40):
    return re.sub(r'[^a-z0-9]+', '-', str(s).lower()).strip('-')[:n] or 'action'


def safe_name(s):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(str(s)))[:80] or 'file'


# ================================================================ authoritative decisions (server side)
# The browser sends what the person typed; everything that matters happens here: the lookups, the verdict, the
# session history that feeds earned autonomy, the signed ledger row, the human-approval rules and the release of
# an agent's answer or a tool call. A user cannot change a verdict by editing the page.
import vf_ledger, vf_engine, vf_auth, vf_tools
import uuid

ENGINE = vf_engine.Engine()
LEDGER = vf_ledger.Ledger(os.path.join(REC, 'ledger.jsonl'))
SESSIONS = vf_auth.Sessions()
DECISIONS = {}
_eval_lock = threading.RLock()
_state = {'ready': False, 'cfg': None, 'policy': None, 'defaults': None, 'models': {'safety': 'wildguard'}}
CONFIG_PATH = os.path.join(vf_ledger.HOME, 'config.json')
CTX = {'agent': 'agent-01', 'status': 'ACTIVE', 'snapshot': True}


def _ensure_engine():
    """Loads the saved settings into the engine and gives it the reversal graph, once."""
    with _eval_lock:
        if _state['ready']:
            return
        d = ENGINE.call('defaults')
        _state['defaults'] = d
        cfg, pol = d['cfg'], d['policy']
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                saved = json.load(f)
            cfg = {**cfg, **saved.get('cfg', {})}
            pol = {**pol, **saved.get('policy', {})}
            if saved.get('models', {}).get('safety') in SAFETY:
                _state['models'] = {'safety': saved['models']['safety']}
        except (OSError, ValueError):
            pass
        _state['cfg'], _state['policy'] = cfg, pol
        ENGINE.call('setPolicy', pol)
        sp = _schema_payload()
        if sp and sp.get('ok'):
            ENGINE.set_schema(sp)
        _state['ready'] = True


def _save_config():
    vf_ledger._private_dir(vf_ledger.HOME)
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({'cfg': _state['cfg'], 'policy': _state['policy'], 'models': _state['models']}, f)


def _enrich(text, want_impact=True):
    """The slow lookups, run in parallel. Anything that should have run but did not is listed in `degraded`, and the
    engine then refuses to wave a non-trivial action through on its own (fail closed)."""
    out = {'wg': {'ok': False, 'error': 'not configured'}, 'policy': {'ok': False}, 'backend': {'ok': False}, 'degraded': []}
    # Disguised text (look-alike letters, spaced-out words, encodings, Hinglish) is read in its plain form too, so the
    # models and lookups see what the rules see. The original text is always kept.
    seen = text
    try:
        variants = ENGINE.call('normalize', {'text': text}, 10)['variants']
        if variants:
            seen = text + ' || plain reading: ' + ' || '.join(variants)[:1500]
    except Exception:
        out['degraded'].append('text normaliser')

    def wg():
        if not _safety_configured():
            return                                   # not configured: the keyword stand-in is labelled in the trace
        r, err = _safety(seen)
        if r is None:
            out['wg'] = {'ok': False, 'error': err}
            out['degraded'].append(SAFETY[_safety_id()]['short'])
        else:
            out['wg'] = {'ok': True, **r}

    def pc():
        try:
            j = _policy_check(seen)
            if j.get('model') is None:
                out['degraded'].append('policy model')
            out['policy'] = {'ok': True, 'model': j.get('model'), 'lookups': j.get('lookups', {})}
        except Exception:
            out['degraded'].append('policy model')

    def im():
        if not want_impact:
            return
        try:
            r = _predict_intent_impact(text, text)
            if r is None:
                out['degraded'].append('impact model')
            else:
                out['backend'] = {'ok': True, **r}
        except Exception:
            out['degraded'].append('impact model')
    ts = [threading.Thread(target=f, daemon=True) for f in (wg, pc, im)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(40)
    return out


def _who(sess):
    u = sess.get('user') if sess else None
    return u['id'] if u else 'guest'


_HARM_WORDS = re.compile(r"\b(kill|murder|shoot|stab|strangl\w*|poison|assassinat\w*|hurt|harm|eliminat\w*|get rid of)\w*\s+(all |every |the |my |his |her |our )?(him|her|them|someone|somebody|people|persons?|customers|clients|employees|staff|colleagues?|neighbou?rs?|wife|husband|boss|child|children)\b|\b(make|let) (him|her|them) disappear|\bend (his|her|their|someone'?s?) life\b|\bhit ?man\b", re.I)


_HARM_PLACES = re.compile(r"\b(blast|blow up|bomb|nuke|torch|burn down|shoot up|massacre)\w*\s+(\w+\s+){0,2}(college|school|university|campus|classroom|office|building|mall|temple|church|mosque|hospital|station|airport|stadium|crowd)\b|\b(kill|murder|stab|shoot|poison)\w*\s+(my|his|her|their)\s+(?!process|job|session|app|cron|tabs?|thread|server)\w+", re.I)


def _reviewer_only(text, res):
    """A held request that may mean harm to a person is never confirmed by the person who typed it: a reviewer decides."""
    if res['verdict'] != 'REVIEW':
        return False
    trig = res.get('trigger', '')
    return 'no_violence' in trig or 'no_harmful_content' in trig or bool(_HARM_WORDS.search(text or '')) or bool(_HARM_PLACES.search(text or ''))


def _store_decision(sess, kind, text, res, **extra):
    did = uuid.uuid4().hex[:12]
    dec = {'id': did, 'kind': kind, 'text': text, 'verdict': res['verdict'], 'tier': res['tier'], 'score': res['score'], 'trigger': res['trigger'],
           'reason': (res.get('explanation') or {}).get('reason', ''), 'requester': _who(sess), 'origin': (sess.setdefault('origin', uuid.uuid4().hex) if isinstance(sess, dict) else None), 'status': 'pending' if res['verdict'] == 'REVIEW' else 'final', 'created': time.time(), 'reviewer_only': _reviewer_only(text, res), **extra}
    with _eval_lock:
        DECISIONS[did] = dec
        while len(DECISIONS) > 2000:
            del DECISIONS[next(iter(DECISIONS))]
    return dec


def _evaluate_core(sess, text, noSession=False, structured=None, enrich_text=None, kind='prompt', ledger_text=None, **extra):
    _ensure_engine()
    if structured and not (enrich_text or text):
        enrich_text = ENGINE.call('describe', {'structured': structured, 'ctx': CTX})['raw']   # the readable form of the call, for the lookups
    enr = _enrich(enrich_text or text)
    with _eval_lock:
        hist = [] if noSession else list(sess['hist'])
        out = ENGINE.call('evaluate', {'text': text, 'structured': structured, 'ctx': CTX, 'hist': hist, 'cfg': _state['cfg'], 'noSession': noSession,
                                       'wg': enr['wg'], 'policy': enr['policy'], 'backend': enr['backend'], 'degraded': enr['degraded']})
        res = out['res']
        if not noSession:
            sess['hist'].append(res['entry'])
        dec = _store_decision(sess, kind, ledger_text or text, res, **extra)
        row = LEDGER.append(res['action']['agent'], ledger_text or text, res['verdict'], res['tier'], res['score'], res['trigger'], actor=_who(sess), decision_id=dec['id'])
        dec['seq'] = row['seq']
    return {'ok': True, 'id': dec['id'], 'res': res, 'hist': hist, 'row': row, 'degraded': enr['degraded'], 'status': dec['status'], 'requester': dec['requester'], 'reviewer_only': dec['reviewer_only']}


def ep_evaluate(sess, sid, d):
    text = str(d.get('text', '')).strip()[:4000]
    if not text:
        return 400, {'ok': False, 'error': 'missing text'}
    r = _evaluate_core(sess, text, noSession=bool(d.get('noSession')))
    if r.get('id') in DECISIONS:
        DECISIONS[r['id']]['ctx'] = _with_history(sess, text)    # so a held request, once approved, is answered with its conversation
    return 200, r


# ---- agent: the LLM drafts, the guardrail decides whether the draft is ever shown ----
_AGENT_SYS = ("You are an assistant that fulfills the user's request directly. A separate safety/authorization system decides afterward "
              "whether your answer is allowed to reach the user — you don't need to judge that yourself, just answer as helpfully and "
              "concretely as you can. Reply with the answer/action result itself (1-4 sentences), no preamble, no markdown, no disclaimers "
              "about needing approval. If the user asks for something 'as a PDF', 'in a file' or 'in Word format', reply with the full text that "
              "belongs in that file (repeat the earlier letter or answer in full); the system turns your reply into the file, so never say you cannot create files.")
_FEEDBACK_SYS = ("The answer you would have given the user was NOT released, because a safety guardrail stopped it before it reached them. "
                 "Write short, honest feedback to the user (2-4 sentences, no markdown, no preamble): acknowledge their request and explain why "
                 "it was stopped, in your own words, using the reason given. If the verdict is REVIEW, say the request is waiting for a person "
                 "to approve it and what would help it get approved; do not say it was blocked. If the verdict is BLOCK, say it will not run and "
                 "say concretely what would need to change.")


def _with_history(sess, text):
    """Follow-ups ('my name is Daksh', 'make it shorter') need the earlier turns of THIS session. Only requests the guardrail
    allowed and answers it released are ever remembered, and a new session starts empty."""
    h = sess.get('chat') or []
    if not h:
        return text
    prev = '\n'.join(f'User: {q}\nAssistant: {a}' for q, a in h[-6:])
    return f'Earlier in this conversation:\n{prev}\n\nNew message from the user (use the earlier turns for context):\n{text}'


def _remember(sess, dec, agent):
    if agent.get('state') == 'released':
        h = sess.setdefault('chat', [])
        h.append((dec['text'][:400], agent['text'][:600]))
        del h[:-6]


def _agent_release(dec, note):
    """Screens a held or fresh draft and returns what the person may see."""
    draft = dec.get('draft') or {}
    if not draft.get('ok'):
        return {'state': 'error', 'error': draft.get('error', 'the LLM did not answer'), 'note': note}
    sc = ENGINE.call('screen', {'text': draft['text']})
    if sc['unsafe']:
        return {'state': 'withheld', 'by': 'output screening', 'note': note, 'reasons': sc['reasons']}
    checked_by = None
    if _safety_configured():
        short = SAFETY[_safety_id()]['short']
        r, err = _safety(dec['text'], draft['text'])
        if r is None:
            return {'state': 'withheld', 'by': short, 'note': note + f' {short} could not check the draft, so it was not shown.', 'reasons': ['safety check unavailable']}
        if r.get('harmful_response'):
            return {'state': 'withheld', 'by': short, 'note': note, 'reasons': [f'{short} judged the draft harmful']}
        checked_by = short
    return {'state': 'released', 'text': sc['text'], 'note': note, 'checked_by': checked_by, 'wildguard': checked_by == 'WildGuard', 'masked': bool(sc['redacted'])}


def _agent_feedback(text, res):
    alt = (res.get('explanation', {}).get('alternatives') or [''])[0]
    out, err = _call_llm(_FEEDBACK_SYS, f"User's request: {text}\nVerdict: {res['verdict']}\nGuardrail's reason: {res.get('explanation', {}).get('reason', '')}\nSystem's own suggested alternative (use if it helps): {alt}", max_tokens=220)
    if out is None:
        return {'ok': False, 'error': err}
    sc = ENGINE.call('screen', {'text': out})
    return {'ok': True, 'text': sc['text'], 'unsafe': sc['unsafe'], 'reasons': sc['reasons']}


def ep_agent_run(sess, sid, d):
    text = str(d.get('input', '')).strip()[:4000]
    if not text:
        return 400, {'ok': False, 'error': 'missing input'}
    if not LLM_READY:
        return 503, {'ok': False, 'error': 'no LLM available (see AGENT_INTEGRATION.md)'}
    box = {}
    hist_text = _with_history(sess, text)

    def draft():
        t, err = _call_llm(_AGENT_SYS, hist_text, max_tokens=200)
        box['d'] = {'ok': t is not None, 'text': t, 'error': err}
    th = threading.Thread(target=draft, daemon=True)
    th.start()
    r = _evaluate_core(sess, text)
    th.join(100)
    dec = DECISIONS[r['id']]
    dec['draft'] = box.get('d', {'ok': False, 'error': 'the LLM took too long'})
    res = r['res']
    if res['verdict'] == 'ALLOW':
        agent = _agent_release(dec, f"Guardrail: ALLOW. {res['explanation']['reason']}")
        _remember(sess, dec, agent)
    else:
        agent = {'state': 'pending' if res['verdict'] == 'REVIEW' else 'withheld', 'by': res['verdict'], 'feedback': _agent_feedback(text, res)}
    dec['agent_state'] = agent['state']
    r['agent'] = agent
    return 200, r


def ep_agent_draft(sess, sid, d):
    """Answer for a request the guardrail has ALREADY allowed (plain Check): the LLM drafts, the draft is screened and shown.
    Nothing is re-evaluated, and a request that was not allowed gets no answer."""
    dec = DECISIONS.get(str(d.get('decision_id', '')))
    if not dec:
        return 404, {'ok': False, 'error': 'unknown decision (it may be from before the server restarted)'}
    if dec.get('verdict') != 'ALLOW' or dec.get('tool') or dec.get('kind') != 'prompt':
        return 409, {'ok': False, 'error': 'only an allowed request gets an answer'}
    if not LLM_READY:
        return 503, {'ok': False, 'error': 'no LLM available'}
    t, err = _call_llm(_AGENT_SYS, _with_history(sess, dec['text']), max_tokens=200)
    dec['draft'] = {'ok': t is not None, 'text': t, 'error': err}
    agent = _agent_release(dec, 'Guardrail: ALLOW.')
    _remember(sess, dec, agent)
    dec['agent_state'] = agent['state']
    return 200, {'ok': True, 'agent': agent}


# ---- human review: only a signed-in reviewer, and never the person who asked ----
def _review_gate(sess, dec):
    u = sess.get('user')
    if not vf_auth.at_least(u, 'reviewer'):
        return 403, {'ok': False, 'error': 'Sign in as a reviewer to decide held actions.', 'need': 'reviewer'}
    if u['id'] == dec['requester']:
        return 403, {'ok': False, 'error': 'You made this request, so someone else has to approve it (maker and checker must differ).'}
    if dec['requester'] == 'guest' and dec.get('origin') and sess.get('origin') == dec['origin']:    # asked as a guest, then signed in as a reviewer in the same window
        return 403, {'ok': False, 'error': 'This request was made in this same window before you signed in, so it counts as yours. Someone else, in their own window, has to approve it (maker and checker must differ).'}
    return None


def _self_gate(sess, dec):
    """The person who made the request may confirm or reject it themselves (a guest counts as the window that asked)."""
    mine = dec['requester'] == _who(sess) and (dec['requester'] != 'guest' or (dec.get('origin') and sess.get('origin') == dec['origin']))
    if not mine:
        return 403, {'ok': False, 'error': 'Only the person who made this request can confirm it themselves. Ask a reviewer instead.'}
    return None


def ep_review(sess, sid, d):
    dec = DECISIONS.get(str(d.get('decision_id', '')))
    if not dec:
        return 404, {'ok': False, 'error': 'unknown decision (it may be from before the server restarted)'}
    selfc = bool(d.get('self'))
    bad = _self_gate(sess, dec) if selfc else _review_gate(sess, dec)
    if bad:
        return bad
    choice = d.get('decision')
    if choice not in ('approved', 'rejected'):
        return 400, {'ok': False, 'error': 'decision must be approved or rejected'}
    approved = choice == 'approved'
    if selfc and approved and dec.get('reviewer_only'):
        return 403, {'ok': False, 'error': 'This request could mean harm to a person, so you cannot approve it yourself. Ask a reviewer, or reject it.'}
    u = sess.get('user') or {'id': 'guest'}
    who = 'requester' if selfc else 'reviewer'
    if selfc and dec.get('status') == 'queued':
        return 409, {'ok': False, 'error': 'This request is waiting for a reviewer, so a reviewer decides it.'}
    with _eval_lock:
        if dec['kind'] == 'doc':
            idxs = list(range(len(dec['edits']))) if d.get('all') else [int(d.get('edit', -1))]
            held = [i for i in idxs if 0 <= i < len(dec['edits']) and dec['edits'][i] == 'held']
            if not held:
                return 409, {'ok': False, 'error': 'nothing held to decide'}
            rows = []
            for i in held:
                dec['edits'][i] = 'applied' if approved else 'blocked'
                lbl = dec['edit_labels'][i]
                rows.append(LEDGER.append(f'human-{who}', f"{'Approved' if approved else 'Rejected'} by the {who}: held edit on line {dec['edit_blocks'][i] + 1}: {lbl}", 'ALLOW' if approved else 'BLOCK',
                                          'MID', dec['edit_scores'][i], f'Human review ({choice})', actor=u['id'], decision_id=dec['id']))
            if 'held' not in dec['edits']:
                dec['status'] = 'done'
            return 200, {'ok': True, 'edits': dec['edits'], 'rows': rows, 'status': dec['status'], 'reviewer': u['id']}
        if dec['status'] not in ('pending', 'queued'):
            return 409, {'ok': False, 'error': f"this request is already {dec['status']}"}
        dec['status'] = choice
        note = str(d.get('note', '')).strip()[:200]
        row = LEDGER.append(f'human-{who}', f"{'Approved' if approved else 'Rejected'} by the {who}: held action: {dec['text'][:70]}" + (f' (note: {note})' if note else ''), 'ALLOW' if approved else 'BLOCK', 'MID', dec['score'],
                            f'Human review ({choice})', actor=u['id'], decision_id=dec['id'])
        out = {'ok': True, 'row': row, 'status': dec['status'], 'reviewer': u['id'], 'by': who}
        dec['decided_by'], dec['note'], dec['review_row'] = u['id'], note, row
        if approved and dec.get('draft') is None and dec.get('kind') == 'prompt' and not dec.get('tool') and LLM_READY and not dec.get('dummy'):
            t, err = _call_llm(_AGENT_SYS, dec.get('ctx') or dec['text'], max_tokens=200)     # a plain Check that was held: write the answer now
            dec['draft'] = {'ok': t is not None, 'text': t, 'error': err}
        if approved and dec.get('draft') is not None:
            out['agent'] = dec['agent_out'] = _agent_release(dec, 'Released after a person approved the request.')
        if approved and dec.get('tool'):
            out['exec'] = _run_tool(dec, 'Tool executed after human approval')
        return 200, out


def ep_review_ask(sess, sid, d):
    """The requester hands a held request to the reviewers' queue instead of confirming it themselves."""
    dec = DECISIONS.get(str(d.get('decision_id', '')))
    if not dec:
        return 404, {'ok': False, 'error': 'unknown decision (it may be from before the server restarted)'}
    bad = _self_gate(sess, dec)
    if bad:
        return bad
    if dec.get('kind') != 'prompt' or dec.get('status') != 'pending':
        return 409, {'ok': False, 'error': 'only a held request that is still undecided can be sent to a reviewer'}
    with _eval_lock:
        dec['status'], dec['asked'] = 'queued', time.time()
        row = LEDGER.append('requester', f"Sent to a reviewer: {dec['text'][:70]}", 'REVIEW', dec.get('tier', 'MID'), dec['score'], 'Requester asked for a reviewer', actor=dec['requester'], decision_id=dec['id'])
    return 200, {'ok': True, 'row': row, 'status': 'queued'}


def ep_review_status(sess, sid, d):
    """What happened to the requests this window sent to the queue (the requester's page polls this)."""
    out = {}
    for i in list(d.get('ids') or [])[:50]:
        dec = DECISIONS.get(str(i))
        if not dec or _self_gate(sess, dec):
            continue
        out[str(i)] = {'status': dec['status'], 'reviewer': dec.get('decided_by'), 'note': dec.get('note', ''), 'row': dec.get('review_row'), 'agent': dec.get('agent_out')} if dec['status'] != 'queued' else {'status': 'queued'}
    return 200, {'ok': True, 'items': out}


def ep_queue(sess, sid, d):
    if not vf_auth.at_least(sess.get('user'), 'reviewer'):
        return 403, {'ok': False, 'error': 'Sign in as a reviewer to see the pending requests.', 'need': 'reviewer'}
    rows = [{'id': x['id'], 'text': x['text'], 'requester': x['requester'], 'score': x['score'], 'tier': x.get('tier', 'MID'), 'reason': x.get('reason', ''),
             'asked': x.get('asked', x['created']), 'sample': bool(x.get('dummy'))} for x in DECISIONS.values() if x.get('status') == 'queued']
    rows.sort(key=lambda r: r['asked'])
    return 200, {'ok': True, 'items': rows, 'me': sess['user']['id']}


# a few example requests so the reviewers' queue is never empty in a demo (they are marked as samples)
_SAMPLES = [
    ('Refund order 7731 for Rs 4,500: customer was charged twice', 'priya', 3.4, 'A refund above the small-value limit needs a second look.', 52),
    ('Delete 120 archived log rows older than 2 years (snapshot taken)', 'rahul', 4.1, 'Deleting data needs a person to confirm the retention window has passed.', 41),
    ('Export the customer list to the partner SFTP for the quarterly audit', 'anita', 4.6, 'Exports containing customer data need a data-owner sign-off.', 27),
    ('Raise the wallet-22 payout limit to Rs 50,000 for vendor Sharma Traders', 'sam', 3.9, 'A limit increase changes how much money can move without a person.', 18),
    ('Update the phone number on customer 4412 after the KYC call', 'kavya', 2.7, 'The agent has not earned enough autonomy for changes to customer records.', 6),
]


def _seed_queue():
    now = time.time()
    for text, who, score, reason, mins in _SAMPLES:
        did = uuid.uuid4().hex[:12]
        DECISIONS[did] = {'id': did, 'kind': 'prompt', 'text': text, 'verdict': 'REVIEW', 'tier': 'MID', 'score': score, 'trigger': 'Decision Engine (score in MID band)', 'reason': reason,
                          'requester': who, 'origin': None, 'status': 'queued', 'created': now - mins * 60, 'asked': now - mins * 60, 'dummy': True}


# ---- documents ----
_INJ_LINE = re.compile(r"(\b(ai|assistant|agent|llm|model|system|bot)\b.{0,50}\b(ignore|disregard|override|instruction|must|should|do the following|obey)\b|ignore (all |any |the )?(previous|prior|above|earlier)|disregard (all |any |the )?(previous|prior|above)|new instructions?|you are now|system prompt|do not (tell|inform|mention).{0,20}(user|anyone)|(forward|send|email|upload|post) .{0,60}(@|http))", re.I)


def _doc_scan(lines, degraded):
    """Looks inside the document for text that talks TO an AI. Lines that look like instructions are checked by the
    policy model and, for the likeliest few, WildGuard. Returns {line index: reason}."""
    flagged = {}
    cand = [(i, l) for i, l in enumerate(lines[:3000]) if l and _INJ_LINE.search(l)]
    clf = _policy_model()
    for n, (i, l) in enumerate(cand[:60]):
        reason = 'text in the document that gives instructions to an AI'
        try:
            m = _policy_check(l[:600]).get('model')
            if m and m['p'] >= 0.6 and m['rule'] != 'none':
                reason = f"text in the document that asks for something prohibited ({m['rule']})"
        except Exception:
            pass
        if n < 6 and _safety_configured():
            r, err = _safety(l[:1500])
            short = SAFETY[_safety_id()]['short']
            if r is None:
                if short not in degraded:
                    degraded.append(short)
            elif r.get('harmful_request'):
                reason = f'text in the document that {short} judged harmful'
        flagged[i] = reason
    return flagged


def ep_doc_evaluate(sess, sid, d):
    doc, plan, instruction = d.get('doc') or {}, d.get('plan') or {}, str(d.get('instruction', ''))[:2000]
    blocks = doc.get('blocks')
    if not isinstance(blocks, list) or len(blocks) > 5000 or not isinstance(plan.get('ops'), list) or len(plan['ops']) > 3000:
        return 400, {'ok': False, 'error': 'bad document or plan'}
    blocks = [str(b)[:5000] for b in blocks]
    ops = []
    for o in plan['ops']:
        if o.get('kind') not in ('redact', 'replace', 'money', 'delete', 'append', 'imgedit') or not isinstance(o.get('block'), int):
            return 400, {'ok': False, 'error': 'bad edit'}
        ops.append(o)
    _ensure_engine()
    enr = _enrich(instruction or 'Review this document', want_impact=False)
    flagged = _doc_scan(blocks, enr['degraded'])
    name = str(doc.get('name', 'document'))[:120]
    with _eval_lock:
        hist0 = list(sess['hist'])
        out = ENGINE.call('docEvaluate', {'doc': {'name': name, 'kind': doc.get('kind'), 'blocks': blocks}, 'plan': {**plan, 'ops': ops}, 'ctx': CTX, 'hist': list(sess['hist']),
                                          'cfg': _state['cfg'], 'wg': enr['wg'], 'policy': enr['policy'], 'degraded': enr['degraded'], 'flagged': flagged})
        res, results = out['res'], out['results']
        sess['hist'].append(res['entry'])
        label = f"Document “{name}”: {instruction[:80]}"
        dec = _store_decision(sess, 'doc', label, res, edits=[r['status'] for r in results], edit_blocks=[r['op']['block'] for r in results],
                              edit_labels=[str(r['op'].get('clause', ''))[:60] for r in results], edit_scores=[r['res']['score'] for r in results])
        dec['status'] = 'pending' if 'held' in dec['edits'] else 'final'
        row = LEDGER.append(res['action']['agent'], label, res['verdict'], res['tier'], res['score'], res['trigger'], actor=_who(sess), decision_id=dec['id'])
        dec['seq'] = row['seq']
    return 200, {'ok': True, 'id': dec['id'], 'res': res, 'results': results, 'row': row, 'degraded': enr['degraded'], 'flagged': flagged, 'hist': hist0, 'status': dec['status'], 'requester': dec['requester']}


# ---- tools: the guardrail stands between an agent's tool call and its effect ----
def _run_tool(dec, why):
    t = dec['tool']
    r = vf_tools.execute(t['name'], t['args'])
    dec['exec'] = r
    LEDGER.append('tool-gateway', f"{t['name']}: {r.get('message') or r.get('error')}", 'ALLOW' if r['ok'] else 'BLOCK', 'LOW', 0, why, actor=dec['requester'], decision_id=dec['id'])
    try:
        fd = os.open(os.path.join(REC, 'tool_runs.jsonl'), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': time.time(), 'decision': dec['id'], 'tool': t['name'], 'args': t['args'], 'result': r}, ensure_ascii=False) + '\n')
    except OSError:
        pass
    return r


def _tool_decision(sess, name, args):
    clean, err = vf_tools.validate(name, args)
    if err:
        return None, (400, {'ok': False, 'error': err})
    r = _evaluate_core(sess, '', structured={'tool': name, 'args': clean}, kind='tool', ledger_text=None, tool={'name': name, 'args': clean})
    return r, None


def ep_tool_call(sess, sid, d):
    r, bad = _tool_decision(sess, str(d.get('tool', '')), d.get('args') or {})
    if bad:
        return bad
    dec = DECISIONS[r['id']]
    if r['res']['verdict'] == 'ALLOW':
        r['exec'] = _run_tool(dec, 'Tool executed: guardrail verdict ALLOW')
    r['tool'] = dec['tool']
    return 200, r


def ep_agent_act(sess, sid, d):
    text = str(d.get('input', '')).strip()[:2000]
    if not text:
        return 400, {'ok': False, 'error': 'missing input'}
    if not LLM_READY:
        return 503, {'ok': False, 'error': 'no LLM available (see AGENT_INTEGRATION.md)'}
    # the person's own words are judged first; a tool is only chosen for a request that is allowed on its face
    first = _evaluate_core(sess, text)
    if first['res']['verdict'] != 'ALLOW':
        first['note'] = 'The request itself was not allowed, so no tool was chosen.'
        return 200, first
    out, err = _call_llm(vf_tools.tool_system_prompt(), text, max_tokens=200, temperature=0)
    if out is None:
        return 502, {'ok': False, 'error': err}
    m = re.search(r'\{.*\}', out, re.S)
    try:
        call = json.loads(m.group(0)) if m else {}
    except ValueError:
        call = {}
    if call.get('tool') in (None, 'none', ''):
        return 200, {**first, 'note': 'The agent did not find a tool that fits this request.'}
    r, bad = _tool_decision(sess, str(call.get('tool')), call.get('args') or {})
    if bad:
        return 200, {**first, 'note': f"The agent proposed an invalid tool call: {bad[1]['error']}", 'proposed': call}
    dec = DECISIONS[r['id']]
    if r['res']['verdict'] == 'ALLOW':
        r['exec'] = _run_tool(dec, 'Tool executed: guardrail verdict ALLOW')
    r['tool'] = dec['tool']; r['proposed'] = call; r['asked'] = text
    return 200, r


# ---- settings (admin only, every change goes into the ledger) ----
def ep_config_get(sess, sid, d):
    _ensure_engine()
    return 200, {'ok': True, 'cfg': _state['cfg'], 'policy': _state['policy'], 'defaults': _state['defaults'], 'can_edit': vf_auth.at_least(sess.get('user'), 'admin')}


def ep_config_set(sess, sid, d):
    u = sess.get('user')
    if not vf_auth.at_least(u, 'admin'):
        return 403, {'ok': False, 'error': 'Only an admin can change risk settings and policy limits. Sign in as an admin.', 'need': 'admin'}
    _ensure_engine()
    old_c, old_p = dict(_state['cfg']), dict(_state['policy'])
    if d.get('reset'):
        new_c, new_p = json.loads(json.dumps(_state['defaults']['cfg'])), dict(_state['defaults']['policy'])
    else:
        new_c = json.loads(json.dumps(old_c)); new_p = dict(old_p)
        for k, v in (d.get('policy') or {}).items():
            if k in new_p and isinstance(v, (int, float)) and v == v and v > 0:
                new_p[k] = v
        c = d.get('cfg') or {}
        for k in ('t1', 't2'):
            if isinstance(c.get(k), (int, float)):
                new_c[k] = c[k]
        for grp in ('w', 'traj'):
            for k, v in (c.get(grp) or {}).items():
                if k in new_c.get(grp, {}) and isinstance(v, (int, float)) and v == v:
                    new_c[grp][k] = v
    if not (new_p['modelReview'] <= new_p['modelThresholdWithWG'] <= new_p['modelThreshold'] <= 1) or new_c['t1'] >= new_c['t2']:
        return 400, {'ok': False, 'error': 'These values are not consistent (review ≤ block with WildGuard ≤ block alone, and the review threshold must be below the block threshold).'}
    changes = [f"{k} {old_p[k]} → {new_p[k]}" for k in new_p if new_p[k] != old_p[k]]
    flat = lambda c: {**{k: v for k, v in c.items() if not isinstance(v, dict)}, **{f'{g}.{k}': v for g in ('w', 'traj') for k, v in c.get(g, {}).items()}}
    fo, fn = flat(old_c), flat(new_c)
    changes += [f"{k} {fo.get(k)} → {fn[k]}" for k in fn if fn[k] != fo.get(k)]
    if not changes:
        return 200, {'ok': True, 'changed': [], 'cfg': _state['cfg'], 'policy': _state['policy']}
    with _eval_lock:
        _state['cfg'], _state['policy'] = new_c, new_p
        ENGINE.call('setPolicy', new_p)
        _save_config()
        row = LEDGER.append('policy-owner', 'Settings changed: ' + '; '.join(changes), 'ALLOW', 'MID', 0, 'Policy configuration change', actor=u['id'])
    return 200, {'ok': True, 'changed': changes, 'row': row, 'cfg': new_c, 'policy': new_p}


# ---- models: which model does which job ----
def _wg_base():
    return WG_URL or 'http://127.0.0.1:8000/v1/completions'


def ep_models_get(sess, sid, d):
    _ensure_engine()
    active = _safety_id()
    return 200, {'ok': True, 'can_edit': vf_auth.at_least(sess.get('user'), 'admin'),
                 'safety': {'active': active, 'options': [
                     {'id': 'wildguard', 'name': SAFETY['wildguard']['name'], 'running': _probe(_wg_base()), 'configured': bool(WG_URL),
                      'note': 'Measured on our tests: flags about 62% of prohibited requests and 3-8% of ordinary ones (more on some sets); about 1 second per request.'},
                     {'id': 'llamaguard', 'name': SAFETY['llamaguard']['name'], 'running': _probe(LG_URL), 'configured': True, 'repo': LG_REPO,
                      'note': 'Not measured yet. Start it with ./start.sh --llamaguard, then run python3 tests/validate_wildguard.py to measure it.'}]},
                 'agent': {'provider': 'this computer' if LLM_LOCAL else 'Groq (hosted)', 'model': LLM_MODEL, 'ready': LLM_READY, 'hosted': not LLM_LOCAL}}


def ep_models_set(sess, sid, d):
    u = sess.get('user')
    if not vf_auth.at_least(u, 'admin'):
        return 403, {'ok': False, 'error': 'Only an admin can change which model does the safety check. Sign in as an admin.', 'need': 'admin'}
    _ensure_engine()
    new = d.get('safety')
    if new not in SAFETY:
        return 400, {'ok': False, 'error': 'unknown safety model'}
    old = _safety_id()
    if new == old:
        return 200, {'ok': True, 'changed': False, 'active': old}
    running = _probe(_wg_base() if new == 'wildguard' else LG_URL)
    with _eval_lock:
        _state['models'] = {'safety': new}
        _save_config()
        row = LEDGER.append('policy-owner', f"Safety model changed: {SAFETY[old]['name']} -> {SAFETY[new]['name']}", 'ALLOW', 'MID', 0, 'Model configuration change', actor=u['id'])
    return 200, {'ok': True, 'changed': True, 'active': new, 'running': running, 'row': row,
                 'warning': None if running else f"{SAFETY[new]['name']} is not running, so requests will be held for a person until you start it."}


# ---- people ----
def ep_login(sess, sid, d):
    u, err = vf_auth.login(d.get('user'), d.get('pin'))
    if not u:
        return 401, {'ok': False, 'error': err}
    portal = d.get('portal')              # the sign-in page has two doors: users (requesters and reviewers) and admins
    if portal == 'admin' and u['role'] != 'admin':
        return 403, {'ok': False, 'error': 'This account is not an admin. Use the User tab.'}
    if portal == 'user' and u['role'] == 'admin':
        return 403, {'ok': False, 'error': 'This is an admin account. Use the Admin tab.'}
    sess['user'] = u
    return 200, {'ok': True, 'user': u}


def ep_logout(sess, sid, d):
    sess['user'] = None
    return 200, {'ok': True}


def ep_session_new(sess, sid, d):
    """Starts a fresh working session (the browser keeps the id). Passing the old id carries the signed-in user over."""
    user = sess.get('user') if sess else None
    return 200, {'ok': True, 'sid': SESSIONS.new(user), 'user': user}


def ep_whoami(sess, sid, d):
    return 200, {'ok': True, 'user': sess.get('user')}


def ep_users(sess, sid, d):
    return 200, {'ok': True, 'users': [{'id': u['id'].strip().lower(), 'name': u['name'], 'role': u['role']} for u in vf_auth.users().values()]}


def ep_tools(sess, sid, d):
    return 200, {'ok': True, 'tools': vf_tools.catalog(), 'sandbox': vf_tools.snapshot_counts()}


def ep_ledger_verify(sess, sid, d):
    return 200, {'ok': True, **LEDGER.verify()}


# route -> (function, needs a session, allowed HTTP methods)
ROUTES = {
    '/api/session/new': (ep_session_new, False, 'POST'), '/api/login': (ep_login, True, 'POST'), '/api/logout': (ep_logout, True, 'POST'),
    '/api/whoami': (ep_whoami, True, 'GET'), '/api/users': (ep_users, False, 'GET'),
    '/api/evaluate': (ep_evaluate, True, 'POST'), '/api/doc/evaluate': (ep_doc_evaluate, True, 'POST'), '/api/review': (ep_review, True, 'POST'),
    '/api/agent/run': (ep_agent_run, True, 'POST'), '/api/agent/act': (ep_agent_act, True, 'POST'), '/api/agent/draft': (ep_agent_draft, True, 'POST'), '/api/review/ask': (ep_review_ask, True, 'POST'), '/api/review/status': (ep_review_status, True, 'POST'), '/api/queue': (ep_queue, True, 'POST'), '/api/tool-call': (ep_tool_call, True, 'POST'),
    '/api/models': (ep_models_get, True, 'GET'), '/api/models/set': (ep_models_set, True, 'POST'),
    '/api/tools': (ep_tools, False, 'GET'), '/api/config': (ep_config_get, True, 'GET'), '/api/config/set': (ep_config_set, True, 'POST'),
    '/api/ledger/verify': (ep_ledger_verify, False, 'GET'),
}


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _guard(self):
        """Only this machine's own page may talk to the API: a page on another site (or a rebinding trick) is refused."""
        host = (self.headers.get('Host') or '')
        if host.rsplit(':', 1)[0].lower() not in ('localhost', '127.0.0.1', '[::1]'):
            self._json(403, {'ok': False, 'error': 'forbidden host'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://{host}',):
            self._json(403, {'ok': False, 'error': 'forbidden origin'})
            return False
        return True

    def _session(self):
        sid = self.headers.get('X-VerifAI-Session', '')
        return sid, SESSIONS.get(sid)

    def _dispatch(self, method):
        """Runs one of the ROUTES. Returns True when the request was handled."""
        from urllib.parse import urlsplit
        path = urlsplit(self.path).path
        route = ROUTES.get(path)
        if not route:
            return False
        fn, needs, m = route
        if m != method:
            self._json(405, {'ok': False, 'error': 'method not allowed'})
            return True
        sid, sess = self._session()
        if needs and not sess:
            self._json(401, {'ok': False, 'error': 'no session: reload the page', 'need': 'session'})
            return True
        body = {}
        if method == 'POST':
            ln = int(self.headers.get('Content-Length', 0))
            if ln > 30 * 1024 * 1024:
                self._json(413, {'ok': False, 'error': 'too large'})
                return True
            try:
                body = json.loads(self.rfile.read(ln)) if ln else {}
            except ValueError:
                self._json(400, {'ok': False, 'error': 'bad JSON'})
                return True
        try:
            code, obj = fn(sess or {}, sid, body)
        except (RuntimeError, TimeoutError) as e:
            code, obj = 503, {'ok': False, 'error': f'decision engine unavailable: {e}', 'unavailable': True}
        except Exception:
            import traceback
            traceback.print_exc()
            code, obj = 500, {'ok': False, 'error': 'internal error'}
        self._json(code, obj)
        return True

    def do_GET(self):
        if not self._guard():
            return
        if self._dispatch('GET'):
            return
        if self.path.startswith('/api/status'):
            n = len(os.listdir(os.path.join(REC, 'actions'))) // 2 if os.path.isdir(os.path.join(REC, 'actions')) else 0
            last = LEDGER.last() or {'seq': 0, 'hash': '00000000'}
            return self._json(200, {'ok': True, 'dir': REC, 'saved': n, 'last_seq': last['seq'], 'last_hash': last['hash'], 'engine': ENGINE.status()['ok']})
        if self.path.startswith('/api/wildguard/status'):
            return self._json(200, {'ok': bool(WG_URL), 'model': WG_MODEL, 'error': None if WG_URL else 'WILDGUARD_URL not set'})
        if self.path.startswith('/api/schema'):
            p = _schema_payload()
            return self._json(200 if p.get('ok') else 503, p)
        if self.path.startswith('/api/ledger'):
            return self._json(200, {'ok': True, 'rows': LEDGER.rows()})
        if self.path.startswith('/api/model-info'):
            info = {'ok': _load_models(), 'version': 'v2' if _model_state.get('impact_v2') is not None else 'v1', 'metrics': None}
            try:
                with open(os.path.join(MODELS, 'model_impact_v2.metrics.json'), encoding='utf-8') as f:
                    info['metrics'] = json.load(f)
            except Exception:
                pass
            try:
                with open(os.path.join(MODELS, 'model_impact_v2.calibration.json'), encoding='utf-8') as f:
                    info['calibration'] = json.load(f)
            except Exception:
                info['calibration'] = None
            return self._json(200, info)
        if self.path.startswith('/api/intent-impact/status'):
            avail = _load_models()
            return self._json(200, {'ok': avail, 'version': 'v2' if _model_state.get('impact_v2') is not None else 'v1', 'error': None if avail else _model_state['error']})
        if self.path.startswith('/api/policy-check/status'):
            sanc, cons = _read_lookup('sanctions.csv'), _read_lookup('consent_lists.csv')
            return self._json(200, {'ok': _policy_model() is not None, 'sanctions': len(sanc or []), 'consent_lists': len(cons or []), 'error': _policy_state['error']})
        if self.path.startswith('/api/agent/status'):
            return self._json(200, {'ok': LLM_READY, 'model': LLM_MODEL, 'local': LLM_LOCAL,
                                     'error': None if LLM_READY else 'no LLM API key set'})
        if not self._static_allowed():
            return self._json(404, {'ok': False, 'error': 'not found'})
        return super().do_GET()

    # Only the web page and its scripts are served. Everything else in this folder (.env with the API key, records,
    # models, user and policy files, the Python source) stays private.
    STATIC_FILES = {'index.html', 'style.css', 'app.js', 'engine.js', 'docs.js', 'chart.umd.min.js', 'jszip.min.js'}

    def _static_allowed(self):
        from urllib.parse import unquote, urlsplit
        p = unquote(urlsplit(self.path).path)
        if p in ('', '/'):
            return True
        parts = [x for x in p.split('/') if x]
        if any(x in ('.', '..') or x.startswith('.') for x in parts):
            return False
        if len(parts) == 1:
            return parts[0] in self.STATIC_FILES
        return parts[0] == 'vendor'

    def do_HEAD(self):
        if not self._static_allowed():
            return self._json(404, {'ok': False})
        return super().do_HEAD()

    def do_POST(self):
        if not self._guard():
            return
        if self._dispatch('POST'):
            return
        if self.path.startswith(('/api/wildguard', '/api/policy-check', '/api/intent-impact', '/api/save', '/api/agent')) and not self._session()[1]:
            return self._json(401, {'ok': False, 'error': 'no session: reload the page', 'need': 'session'})
        if self.path.startswith('/api/wildguard'):
            ln = int(self.headers.get('Content-Length', 0))
            if ln > MAX:
                return self._json(413, {'ok': False, 'error': 'too large'})
            try:
                d = json.loads(self.rfile.read(ln)) if ln else {}
            except Exception as e:
                return self._json(400, {'ok': False, 'error': str(e)})
            result, err = _safety(str(d.get('prompt', '')), str(d.get('response', '')))
            if result is None:
                return self._json(503, {'ok': False, 'error': err})
            return self._json(200, {'ok': True, **result})
        if self.path.startswith('/api/policy-check'):
            ln = int(self.headers.get('Content-Length', 0))
            if ln > MAX:
                return self._json(413, {'ok': False, 'error': 'too large'})
            try:
                d = json.loads(self.rfile.read(ln)) if ln else {}
                return self._json(200, {'ok': True, **_policy_check(str(d.get('text', '')))})
            except Exception as e:
                return self._json(400, {'ok': False, 'error': str(e)})
        if self.path.startswith('/api/intent-impact'):
            ln = int(self.headers.get('Content-Length', 0))
            if ln > MAX:
                return self._json(413, {'ok': False, 'error': 'too large'})
            try:
                d = json.loads(self.rfile.read(ln))
                result = _predict_intent_impact(d.get('declared_intent', ''), d.get('action_text', ''))
                if result is None:
                    return self._json(503, {'ok': False, 'error': _model_state['error'] or 'models unavailable'})
                return self._json(200, {'ok': True, **result})
            except Exception as e:
                return self._json(400, {'ok': False, 'error': str(e)})
        if self.path.startswith(('/api/agent/output', '/api/agent/feedback')):
            return self._json(410, {'ok': False, 'error': 'removed: use /api/agent/run, which only releases an answer after the guardrail decides'})
        if not self.path.startswith('/api/save'):
            return self._json(404, {'ok': False})
        ln = int(self.headers.get('Content-Length', 0))
        if ln > MAX:
            return self._json(413, {'ok': False, 'error': 'too large'})
        try:
            d = json.loads(self.rfile.read(ln))
            row = next((r for r in LEDGER.rows()[-200:] if r['seq'] == int(d.get('seq', -1))), None)
            if not row:
                return self._json(400, {'ok': False, 'error': 'unknown ledger entry'})
            os.makedirs(os.path.join(REC, 'actions'), exist_ok=True)
            os.makedirs(os.path.join(REC, 'outputs'), exist_ok=True)
            saved = []
            if d.get('report') is not None:
                base = f"{int(row['seq']):03d}_{slug(row['verdict'])}_{slug(vf_ledger.mask_pii(d.get('title', '')))}"
                def put(path, data, mode='w'):
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, mode, **({} if 'b' in mode else {'encoding': 'utf-8'})) as f:
                        f.write(data)
                put(os.path.join(REC, 'actions', base + '.json'), vf_ledger.mask_pii(json.dumps(d.get('data', {}), indent=2, ensure_ascii=False)))
                put(os.path.join(REC, 'actions', base + '.html'), vf_ledger.mask_pii(d['report']))
                saved += [base + '.json', base + '.html']
                if d.get('output'):
                    name = f"{int(row['seq']):03d}_{safe_name(d['output']['name'])}"
                    put(os.path.join(REC, 'outputs', name), base64.b64decode(d['output']['b64']), 'wb')
                    saved.append('outputs/' + name)
            return self._json(200, {'ok': True, 'saved': saved, 'dir': REC})
        except Exception as e:  # keep the app running whatever the payload
            return self._json(400, {'ok': False, 'error': str(e)})

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    PORT = int(os.environ.get('VERIFAI_PORT', '8765'))
    for private in (os.path.join(ROOT, '.env'),):                 # secrets are readable by this user only
        try:
            os.chmod(private, 0o600)
        except OSError:
            pass
    os.makedirs(REC, exist_ok=True)
    try:
        os.chmod(REC, 0o700)
    except OSError:
        pass
    _seed_queue()
    threading.Thread(target=lambda: (_load_models(), _policy_model(), _schema_payload(), _ensure_engine()), daemon=True).start()   # warm up so the first request isn't slow
    print(f'VerifAI on http://localhost:{PORT}  ·  saving to {REC}')
    try:
        ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
    except OSError:
        print(f'Port {PORT} is already in use. VerifAI is probably already running: open http://localhost:{PORT}\n'
              f'To restart it, stop the old one first:  lsof -ti :{PORT} | xargs kill')
