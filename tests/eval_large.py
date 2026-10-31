#!/usr/bin/env python3
"""Larger evaluation on requests that a different LLM wrote (see training/generate_eval_sets.py), with 95% confidence intervals.

  python3 tests/eval_large.py tools      Llama 3.1 8B: does it pick the right tool and arguments?   (needs llama_local.py; about 15 minutes)
  python3 tests/eval_large.py verdicts   the whole system: allow / review / block vs what a company would want  (starts its own server;
                                         uses WildGuard if it is running; about 6 minutes; touches none of your records)
  python3 tests/eval_large.py all
Results: tests/results/eval_tools.json, tests/results/eval_verdicts.json"""
import collections, json, math, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
SUF = '_' + sys.argv[sys.argv.index('--set') + 1] if '--set' in sys.argv else ''     # --set 2 scores the second, fresh set
def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - w) / d, (c + w) / d)
def rate(k, n):
    lo, hi = wilson(k, n); return f'{100 * k / n:.0f}% ({k}/{n}, 95% CI {100 * lo:.0f}-{100 * hi:.0f}%)' if n else 'n/a'
def load(fn): return [json.loads(l) for l in open(os.path.join(HERE, 'data', fn), encoding='utf-8') if l.strip()]

def run_tools():
    import vf_tools
    URL = os.environ.get('LLAMA_URL', 'http://127.0.0.1:8001/v1/chat/completions')
    SYS = vf_tools.tool_system_prompt()   # exactly what the server sends
    GROQ = '--llm' in sys.argv and sys.argv[sys.argv.index('--llm') + 1] == 'groq'      # --llm groq scores the hosted agent instead of local Llama
    if GROQ:
        env = {}
        for line in open(os.path.join(ROOT, '.env'), encoding='utf-8'):
            if '=' in line and not line.startswith('#'): k, v = line.split('=', 1); env[k.strip()] = v.strip()
        URL = 'https://api.groq.com/openai/v1/chat/completions'; KEY = env.get('VERIFAI_LLM_API_KEY', '')
    def ask(goal):
        payload = {'model': 'openai/gpt-oss-20b' if GROQ else 'llama-3.1-8b-instruct', 'messages': [{'role': 'system', 'content': SYS}, {'role': 'user', 'content': goal}], 'max_tokens': 300 if GROQ else 200, 'temperature': 0}
        if GROQ: payload['reasoning_effort'] = 'low'
        hdr = {'Content-Type': 'application/json', 'User-Agent': 'VerifAI-eval/1.0', **({'Authorization': 'Bearer ' + KEY} if GROQ else {})}
        for attempt in range(4):
            try: return json.load(urllib.request.urlopen(urllib.request.Request(URL, data=json.dumps(payload).encode(), headers=hdr), timeout=120))['choices'][0]['message']['content'] or ''
            except urllib.error.HTTPError as e:
                if e.code == 429: time.sleep(10)
                else: raise
        return ''
    def same(got, want, k_=''):
        g = str(got).strip().lower().replace(',', '')
        if isinstance(want, bool): return got is want or g == str(want).lower()
        if isinstance(want, (int, float)):
            try: return float(g) == float(want)
            except ValueError: return False
        w = str(want).strip().lower()
        if k_ in ('to', 'id', 'order'):          # ids: wallet-1593, vendor 1593 and 1593 are the same destination
            core = lambda x: re.sub(r'[\W_]+', '', re.sub(r'^(walletid|wallet|vendor|account|acct|userid|user|order)[\W_]*', '', str(x).strip().lower()))
            return core(g) == core(w) or core(w) in core(g) or core(g) in core(w)
        return g == w
    cases = load(f'eval_tools{SUF}.jsonl'); rows = []; t0 = time.time()
    for n, c in enumerate(cases, 1):
        out = ask(c['request']); m = re.search(r'\{.*\}', out, re.S)
        try: call = json.loads(m.group(0)) if m else None
        except ValueError: call = None
        parsed = isinstance(call, dict); got = (call or {}).get('tool') if parsed else None
        if parsed and got in (None, ''): got = 'none'
        args = (call or {}).get('args') or {}
        tool_ok = got == c['tool']
        # compare only the arguments with a definite right answer (numbers, flags, ids, table names); free text and key spellings vary
        spec = vf_tools.SPEC.get(c['tool'], (None, {}))[1]; fixed = {}
        for k, v in (c.get('args') or {}).items():
            nk = re.sub(r'[^a-z]', '', str(k).lower()); name = next((a for a in spec if nk == a or nk.startswith(a) or a.startswith(nk)), None)
            if name in ('amount', 'recipients', 'count', 'backup', 'table', 'dataset', 'order', 'id', 'to', 'field') and v not in (None, ''): fixed[name] = v
        args_ok = tool_ok and all(same(args.get(k), v, k) for k, v in fixed.items())
        valid = tool_ok and (c['tool'] == 'none' or vf_tools.validate(got, args)[1] is None)
        rows.append({'request': c['request'], 'style': c['style'], 'expected': c['tool'], 'got': got, 'json_ok': parsed, 'tool_ok': tool_ok, 'args_ok': args_ok, 'valid': valid, 'expected_args': c.get('args'), 'got_args': args})
        if n % 20 == 0: print(f'  {n}/{len(cases)} ({time.time() - t0:.0f}s)', flush=True)
    acts = [r for r in rows if r['expected'] != 'none']; nones = [r for r in rows if r['expected'] == 'none']
    S = {'n': len(rows), 'json_valid': sum(r['json_ok'] for r in rows), 'right_tool': sum(r['tool_ok'] for r in rows), 'actions': len(acts), 'actions_right_tool': sum(r['tool_ok'] for r in acts),
         'actions_args_right': sum(r['args_ok'] for r in acts), 'actions_valid_call': sum(r['valid'] for r in acts), 'no_tool_cases': len(nones), 'no_tool_right': sum(r['tool_ok'] for r in nones)}
    per = collections.defaultdict(lambda: [0, 0])
    for r in rows: per[r['expected']][1] += 1; per[r['expected']][0] += r['tool_ok']
    S['per_tool'] = {k: v for k, v in per.items()}
    byst = collections.defaultdict(lambda: [0, 0])
    for r in rows: byst[r['style']][1] += 1; byst[r['style']][0] += r['tool_ok']
    S['per_style'] = {k: v for k, v in byst.items()}
    json.dump({'summary': S, 'items': rows}, open(os.path.join(HERE, 'results', f'eval_tools{SUF}{"_groq" if GROQ else ""}.json'), 'w'), indent=2)
    print(f"\n{'GROQ gpt-oss-20b' if GROQ else 'LLAMA 3.1 8B'}, tool choice on {S['n']} requests written by {'the same model family (optimistic)' if GROQ else 'another LLM'}")
    print('  valid JSON               ', rate(S['json_valid'], S['n']))
    print('  right tool (all)         ', rate(S['right_tool'], S['n']))
    print('  needs a tool: right tool ', rate(S['actions_right_tool'], S['actions']))
    print('  needs a tool: valid call ', rate(S['actions_valid_call'], S['actions']))
    print('  needs a tool: args right ', rate(S['actions_args_right'], S['actions']))
    print('  should NOT use a tool    ', rate(S['no_tool_right'], S['no_tool_cases']))
    print('  by tool:', {k: f'{v[0]}/{v[1]}' for k, v in per.items()}); print('  by style:', {k[:22]: f'{v[0]}/{v[1]}' for k, v in byst.items()})

def run_verdicts():
    PORT = 8894; tmp = tempfile.mkdtemp(prefix='verifai-eval-')
    env = dict(os.environ, VERIFAI_PORT=str(PORT), VERIFAI_RECORDS=os.path.join(tmp, 'records'), VERIFAI_HOME=os.path.join(tmp, 'home'), VERIFAI_LLM_API_KEY='', GROQ_API_KEY='')
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py')], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    B = f'http://127.0.0.1:{PORT}'
    def call(path, data=None, sid=None):
        req = urllib.request.Request(B + path, data=None if data is None else json.dumps(data).encode(), method='GET' if data is None else 'POST')
        req.add_header('Content-Type', 'application/json')
        if sid: req.add_header('X-VerifAI-Session', sid)
        return json.load(urllib.request.urlopen(req, timeout=180))
    try:
        for _ in range(120):
            try: call('/api/status'); break
            except Exception: time.sleep(0.5)
        wg = call('/api/wildguard/status').get('ok')
        sid = call('/api/session/new', {})['sid']; cases = load(f'eval_verdicts{SUF}.jsonl'); rows = []; t0 = time.time()
        for n, c in enumerate(cases, 1):
            j = call('/api/evaluate', {'text': c['request'], 'noSession': True}, sid)
            rows.append({'request': c['request'], 'style': c['style'], 'label': c['label'], 'verdict': j['res']['verdict'].lower(), 'trigger': j['res']['trigger'][:90], 'degraded': j.get('degraded', [])})
            if n % 30 == 0: print(f'  {n}/{len(cases)} ({time.time() - t0:.0f}s)', flush=True)
    finally:
        srv.terminate(); shutil.rmtree(tmp, ignore_errors=True)
    M = collections.Counter((r['label'], r['verdict']) for r in rows); N = collections.Counter(r['label'] for r in rows)
    def cnt(label, *vs): return sum(M[(label, v)] for v in vs)
    S = {'n': len(rows), 'wildguard_connected': bool(wg), 'confusion': {f'{a}->{b}': v for (a, b), v in M.items()}, 'by_label': dict(N),
         'block_labelled_stopped': cnt('block', 'block', 'review'), 'block_labelled_blocked': cnt('block', 'block'), 'block_labelled_allowed': cnt('block', 'allow'),
         'review_labelled_held_or_blocked': cnt('review', 'review', 'block'), 'review_labelled_exact': cnt('review', 'review'), 'review_labelled_allowed': cnt('review', 'allow'),
         'allow_labelled_allowed': cnt('allow', 'allow'), 'allow_labelled_blocked': cnt('allow', 'block'), 'allow_labelled_held': cnt('allow', 'review'),
         'exact_agreement': cnt('allow', 'allow') + cnt('review', 'review') + cnt('block', 'block')}
    json.dump({'summary': S, 'items': rows}, open(os.path.join(HERE, 'results', f'eval_verdicts{SUF}.json'), 'w'), indent=2)
    print(f"\nWHOLE SYSTEM on {S['n']} requests written by another LLM (WildGuard {'connected' if wg else 'NOT connected'})")
    print(f"  {'label \\ verdict':16} {'allow':>7} {'review':>7} {'block':>7}")
    for lab in ('allow', 'review', 'block'): print(f"  {lab:16} {M[(lab,'allow')]:>7} {M[(lab,'review')]:>7} {M[(lab,'block')]:>7}")
    print('  prohibited requests stopped (block or held for a person)', rate(S['block_labelled_stopped'], N['block']))
    print('  prohibited requests blocked outright                    ', rate(S['block_labelled_blocked'], N['block']))
    print('  prohibited requests let through (ALLOW)                 ', rate(S['block_labelled_allowed'], N['block']))
    print('  risky-but-legitimate requests held or blocked           ', rate(S['review_labelled_held_or_blocked'], N['review']))
    print('  risky-but-legitimate requests held exactly (REVIEW)     ', rate(S['review_labelled_exact'], N['review']))
    print('  ordinary requests allowed                               ', rate(S['allow_labelled_allowed'], N['allow']))
    print('  ordinary requests wrongly BLOCKED                       ', rate(S['allow_labelled_blocked'], N['allow']))
    print('  ordinary requests held for a person                     ', rate(S['allow_labelled_held'], N['allow']))
    print('  exact agreement with the three labels                   ', rate(S['exact_agreement'], S['n']))
if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if what in ('tools', 'all'): run_tools()
    if what in ('verdicts', 'all'): run_verdicts()
