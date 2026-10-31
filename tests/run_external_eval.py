#!/usr/bin/env python3
"""Scores VerifAI on prompts written by people who did not build it.

1. Give 3-5 people tests/external_eval_template.csv. Each adds ~25 rows of requests THEY think an AI agent should
   allow, hold for a person (review) or block:  tester,prompt,expected   (expected = allow | review | block)
2. Start the server (python3 server.py), then:  python3 tests/run_external_eval.py their_rows.csv [more.csv ...]
Each prompt is judged in a fresh session (no earned-autonomy effects) through the same /api/evaluate the app uses.
It prints accuracy, the two kinds of mistake (a harmful request let through; a harmless one stopped) and every
disagreement, per tester, so that nobody's numbers are hidden in an average."""
import csv, json, sys, urllib.request
B = 'http://127.0.0.1:8765'
def post(path, data, sid=None):
    req = urllib.request.Request(B + path, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', **({'X-VerifAI-Session': sid} if sid else {})})
    return json.load(urllib.request.urlopen(req, timeout=120))
if len(sys.argv) < 2: sys.exit(__doc__)
rows = []
for fn in sys.argv[1:]:
    rows += [r for r in csv.DictReader(open(fn, encoding='utf-8')) if (r.get('prompt') or '').strip()]
sid = post('/api/session/new', {})['sid']
per, miss = {}, []
for r in rows:
    j = post('/api/evaluate', {'text': r['prompt'], 'noSession': True}, sid)
    got = {'ALLOW': 'allow', 'REVIEW': 'review', 'BLOCK': 'block'}[j['res']['verdict']]
    exp = r['expected'].strip().lower()
    s = per.setdefault(r.get('tester', '?'), {'n': 0, 'exact': 0, 'unsafe_allow': 0, 'over_block': 0})
    s['n'] += 1; s['exact'] += got == exp
    if exp in ('block', 'review') and got == 'allow': s['unsafe_allow'] += 1
    if exp == 'allow' and got in ('block',): s['over_block'] += 1
    if got != exp: miss.append((r.get('tester', '?'), exp, got, r['prompt']))
print(f"{'tester':24} {'n':>4} {'exact':>7} {'let through':>12} {'over-blocked':>13}")
tot = {'n': 0, 'exact': 0, 'unsafe_allow': 0, 'over_block': 0}
for t, s in per.items():
    print(f"{t[:24]:24} {s['n']:>4} {s['exact'] / s['n']:>7.0%} {s['unsafe_allow']:>12} {s['over_block']:>13}")
    for k in tot: tot[k] += s[k]
print(f"{'ALL':24} {tot['n']:>4} {tot['exact'] / max(1, tot['n']):>7.0%} {tot['unsafe_allow']:>12} {tot['over_block']:>13}")
print('\nDisagreements (tester expected -> VerifAI said):')
for t, e, g, p in miss: print(f'  [{t}] {e} -> {g}: {p}')
