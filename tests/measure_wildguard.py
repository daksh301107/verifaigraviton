#!/usr/bin/env python3
"""What does each AI component add? Runs the same prompts through the decision engine four ways and reports how
many violations are stopped and how many ordinary requests are wrongly stopped.

  A  rules only (hard rules, text normaliser, heuristics)
  B  A + the policy model
  C  A + WildGuard
  D  A + policy model + WildGuard   (what the product does)

Needs the server running (python3 server.py, with WildGuard connected).  About 6 minutes (WildGuard answers one prompt a second).
Results are cached in tests/results/, so a second run is quick.   python3 tests/measure_wildguard.py [--fresh]"""
import hashlib, json, os, random, subprocess, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
B = os.environ.get('VERIFAI_URL', 'http://127.0.0.1:8765')
CACHE = os.path.join(HERE, 'results', 'wildguard_cache.json')
OUT = os.path.join(HERE, 'results', 'wildguard_eval.json')
cache = {} if '--fresh' in sys.argv or not os.path.exists(CACHE) else json.load(open(CACHE))

def call(path, data=None, sid=None):
    req = urllib.request.Request(B + path, data=None if data is None else json.dumps(data).encode(), method='GET' if data is None else 'POST')
    req.add_header('Content-Type', 'application/json')
    if sid: req.add_header('X-VerifAI-Session', sid)
    return json.load(urllib.request.urlopen(req, timeout=180))
def node(mode, items):
    p = subprocess.run(['node', os.path.join(HERE, 'eval_conditions.js'), mode], input=json.dumps(items), capture_output=True, text=True, cwd=ROOT)
    if p.returncode: sys.exit(p.stderr)
    return json.loads(p.stdout)

# ---- the prompt sets
cases = json.load(open(os.path.join(HERE, 'hard_policy_cases.json')))
para = json.load(open(os.path.join(HERE, 'hard_policy_paraphrases.json'))); para.pop('note', None)
sets = {'known violations': [t for c in cases.values() for t in c.get('block', [])],
        'reworded violations': [t for r, ts in para.items() if r != 'ok' for t in ts]}
lib = subprocess.run(['node', '-e', "const L=require('./tests/adversarial_lib.js'); const o={}; for (const [k,f] of Object.entries(L.T)) if (k!=='plain') o[k]=L.base.filter((_,i)=>i%11===0).map(f); o.hinglish=L.HI; console.log(JSON.stringify(o))"], capture_output=True, text=True, cwd=ROOT)
sets['disguised violations'] = [t for ts in json.loads(lib.stdout).values() for t in ts]
rnd = random.Random(5)
neg = [json.loads(l) for l in open(os.path.join(ROOT, 'training', 'data', 'generated.jsonl'), encoding='utf-8') if l.strip()]
low = [r['text'] for r in neg if r['label'] == 'LOW']; mid = [r['text'] for r in neg if r['label'] == 'MID']
sets['look-alike requests'] = [t for c in cases.values() for t in c.get('ok', [])] + para['ok']          # same topics as the rules, but allowed
sets['routine requests (low impact)'] = rnd.sample(low, 40)                                               # reading, tickets, calendar, small edits
sets['routine requests (medium impact)'] = rnd.sample(mid, 40)                                            # real changes: refunds, payments, record edits
items = [{'id': f'{g}|{i}', 'group': g, 'text': t} for g, ts in sets.items() for i, t in enumerate(ts)]
print({g: len(ts) for g, ts in sets.items()}, 'total', len(items))

# ---- what the models and lists say (cached)
sid = call('/api/session/new', {})['sid']
seen = {x['id']: x['seen'] for x in node('seen', [{'id': i['id'], 'text': i['text']} for i in items])}
t0 = time.time()
for n, it in enumerate(items, 1):
    key = hashlib.sha1(seen[it['id']].encode()).hexdigest()
    if key not in cache:
        wg = call('/api/wildguard', {'prompt': seen[it['id']]}, sid)
        pol = call('/api/policy-check', {'text': seen[it['id']]}, sid)
        imp = call('/api/intent-impact', {'declared_intent': seen[it['id']], 'action_text': seen[it['id']]}, sid)
        cache[key] = {'wg': wg, 'policy': {'ok': pol.get('ok'), 'model': pol.get('model'), 'lookups': pol.get('lookups', {})}, 'backend': imp}
        if n % 25 == 0:
            json.dump(cache, open(CACHE, 'w')); print(f'  {n}/{len(items)} ({time.time() - t0:.0f}s)', flush=True)
    it.update(cache[key])
json.dump(cache, open(CACHE, 'w'))
res = {r['id']: r for r in node('verdicts', [{k: it[k] for k in ('id', 'text', 'wg', 'policy', 'backend')} for it in items])}

# ---- report
def pct(a, b): return f'{100 * a / b:.0f}%' if b else '-'
report = {}
for g in sets:
    its = [i for i in items if i['group'] == g]; n = len(its)
    row = {'n': n, 'wildguard_flags': sum(1 for i in its if i['wg'].get('ok') and i['wg'].get('harmful_request'))}
    for c in 'ABCD':
        row[c] = {'block': sum(1 for i in its if res[i['id']][c]['v'] == 'BLOCK'), 'not_allowed': sum(1 for i in its if res[i['id']][c]['v'] != 'ALLOW')}
    report[g] = row
json.dump({'summary': report, 'items': [{'group': i['group'], 'text': i['text'], 'wg_harmful': (i['wg'].get('harmful_request') if i['wg'].get('ok') else None), **{c: res[i['id']][c] for c in 'ABCD'}} for i in items]}, open(OUT, 'w'), indent=2)
names = {'A': 'rules only', 'B': '+ policy model', 'C': '+ WildGuard', 'D': 'both (product)'}
print('\nVIOLATIONS: share BLOCKED (share stopped = blocked or sent to a person).  Higher is better.')
for g in ('known violations', 'reworded violations', 'disguised violations'):
    r = report[g]; print(f"\n{g} (n={r['n']}), WildGuard on its own flags {pct(r['wildguard_flags'], r['n'])}")
    for c in 'ABCD': print(f"   {names[c]:16} blocked {pct(r[c]['block'], r['n']):>4}   stopped {pct(r[c]['not_allowed'], r['n']):>4}")
for g in ('look-alike requests', 'routine requests (low impact)', 'routine requests (medium impact)'):
    r = report[g]
    print(f"\n{g.upper()} (n={r['n']}): share wrongly BLOCKED, and share sent to a person or blocked.  Lower is better. WildGuard on its own flags {pct(r['wildguard_flags'], r['n'])}")
    for c in 'ABCD': print(f"   {names[c]:16} blocked {pct(r[c]['block'], r['n']):>4}   sent to a person or blocked {pct(r[c]['not_allowed'], r['n']):>4}")
print('\nwrote', OUT)
