#!/usr/bin/env python3
"""Validates WildGuard on its own, as a yes/no classifier ("is this request harmful?"), on labelled requests written by another LLM.

Positives  = requests labelled 'block' (clearly prohibited for a company's agent)
Negatives  = 'allow' (ordinary), 'review' (risky but legitimate) and the ordinary tool requests of set 3
No engine, no rules, no models of ours: only WildGuard's own answer on the raw text.  Needs the server and WildGuard running.
Runs about 12 minutes the first time (one request a second), then is cached.   python3 tests/validate_wildguard.py"""
import collections, hashlib, json, math, os, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
B = os.environ.get('VERIFAI_URL', 'http://127.0.0.1:8765')
CACHE = os.path.join(HERE, 'results', 'wg_only_cache.json'); OUT = os.path.join(HERE, 'results', 'wildguard_validation.json')
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
def call(path, data=None, sid=None):
    req = urllib.request.Request(B + path, data=None if data is None else json.dumps(data).encode(), method='GET' if data is None else 'POST')
    req.add_header('Content-Type', 'application/json')
    if sid: req.add_header('X-VerifAI-Session', sid)
    return json.load(urllib.request.urlopen(req, timeout=120))
def load(fn): return [json.loads(l) for l in open(os.path.join(HERE, 'data', fn), encoding='utf-8') if l.strip()]
def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)); return ((c - w) / d, (c + w) / d)
def pct(k, n):
    lo, hi = wilson(k, n); return f'{100 * k / n:.0f}% ({k}/{n}, CI {100 * lo:.0f}-{100 * hi:.0f}%)' if n else 'n/a'
items = []
for suf in ('', '_2', '_3'):
    for r in load(f'eval_verdicts{suf}.jsonl'): items.append({'set': suf or '_1', 'group': {'block': 'prohibited', 'allow': 'ordinary', 'review': 'risky but legitimate'}[r['label']], 'style': r['style'], 'text': r['request']})
for r in load('eval_tools_3.jsonl'): items.append({'set': '_3', 'group': 'ordinary tool request', 'style': r['style'], 'text': r['request']})
sid = call('/api/session/new', {})['sid']; t0 = time.time(); lat = []
for n, it in enumerate(items, 1):
    key = hashlib.sha1(it['text'].encode()).hexdigest()
    if key not in cache:
        s = time.time(); j = call('/api/wildguard', {'prompt': it['text']}, sid); lat.append(time.time() - s)
        cache[key] = {'ok': bool(j.get('ok')), 'harmful': j.get('harmful_request') if j.get('ok') else None}
        if n % 40 == 0: json.dump(cache, open(CACHE, 'w')); print(f'  {n}/{len(items)} ({time.time() - t0:.0f}s)', flush=True)
    it['wg'] = cache[key]
json.dump(cache, open(CACHE, 'w'))
bad = [i for i in items if not i['wg']['ok']]
items = [i for i in items if i['wg']['ok']]
P = [i for i in items if i['group'] == 'prohibited']
N_ord = [i for i in items if i['group'] == 'ordinary']; N_rev = [i for i in items if i['group'] == 'risky but legitimate']; N_tool = [i for i in items if i['group'] == 'ordinary tool request']
flag = lambda xs: sum(1 for i in xs if i['wg']['harmful'])
def prf(pos, neg):
    tp = flag(pos); fn = len(pos) - tp; fp = flag(neg); tn = len(neg) - fp
    prec = tp / (tp + fp) if tp + fp else 0; rec = tp / len(pos); f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    return {'tp': tp, 'fn': fn, 'fp': fp, 'tn': tn, 'precision': prec, 'recall': rec, 'f1': f1, 'accuracy': (tp + tn) / (len(pos) + len(neg)), 'false_alarm_rate': fp / len(neg)}
S = {'n': len(items), 'unanswered': len(bad), 'prohibited': len(P), 'flagged_prohibited': flag(P), 'ordinary': len(N_ord), 'flagged_ordinary': flag(N_ord), 'risky_legit': len(N_rev), 'flagged_risky_legit': flag(N_rev),
     'ordinary_tool': len(N_tool), 'flagged_ordinary_tool': flag(N_tool),
     'vs_ordinary': prf(P, N_ord), 'vs_all_legitimate': prf(P, N_ord + N_rev + N_tool),
     'by_set': {s: {'recall': flag([i for i in P if i['set'] == s]), 'n_pos': len([i for i in P if i['set'] == s]), 'false_alarms': flag([i for i in N_ord if i['set'] == s]), 'n_neg': len([i for i in N_ord if i['set'] == s])} for s in ('_1', '_2', '_3')},
     'recall_by_style': {}, 'false_alarm_by_style': {}, 'avg_seconds': round(sum(lat) / len(lat), 2) if lat else None}
for st in sorted({i['style'] for i in P}): xs = [i for i in P if i['style'] == st]; S['recall_by_style'][st] = [flag(xs), len(xs)]
for st in sorted({i['style'] for i in N_ord + N_tool}): xs = [i for i in N_ord + N_tool if i['style'] == st]; S['false_alarm_by_style'][st] = [flag(xs), len(xs)]
miss = [i['text'] for i in P if not i['wg']['harmful']]; fa = [i['text'] for i in N_ord + N_tool if i['wg']['harmful']]
json.dump({'summary': S, 'missed_prohibited': miss, 'false_alarms': fa}, open(OUT, 'w'), indent=2)
a, b = S['vs_ordinary'], S['vs_all_legitimate']
print(f"\nWILDGUARD ON ITS OWN, {S['n']} requests ({S['unanswered']} unanswered)")
print('  recall (prohibited requests it flags)          ', pct(S['flagged_prohibited'], S['prohibited']))
print('  false alarms on ordinary requests              ', pct(S['flagged_ordinary'], S['ordinary']))
print('  false alarms on ordinary tool requests         ', pct(S['flagged_ordinary_tool'], S['ordinary_tool']))
print('  flags on risky-but-legitimate requests         ', pct(S['flagged_risky_legit'], S['risky_legit']))
print(f"  vs ordinary only:     precision {a['precision']:.2f}  recall {a['recall']:.2f}  F1 {a['f1']:.2f}  accuracy {a['accuracy']:.2f}")
print(f"  vs all legitimate:    precision {b['precision']:.2f}  recall {b['recall']:.2f}  F1 {b['f1']:.2f}  accuracy {b['accuracy']:.2f}  false-alarm rate {b['false_alarm_rate']:.2f}")
print('  recall by set:', {k: f"{v['recall']}/{v['n_pos']}" for k, v in S['by_set'].items()}, '| false alarms by set:', {k: f"{v['false_alarms']}/{v['n_neg']}" for k, v in S['by_set'].items()})
print('  recall by style:', {k[:24]: f'{v[0]}/{v[1]}' for k, v in S['recall_by_style'].items()})
print('  false alarms by style:', {k[:24]: f'{v[0]}/{v[1]}' for k, v in S['false_alarm_by_style'].items()})
print('  seconds per call:', S['avg_seconds'])
