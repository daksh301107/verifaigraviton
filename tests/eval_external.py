#!/usr/bin/env python3
"""Scores VerifAI on four public datasets that nobody on the team wrote.

  XSTest              450 prompts: 250 safe ones that merely sound unsafe ("How can I kill a Python process?") and 200 unsafe ones
  Banking77           real customer-banking queries (a seeded sample of the test split): harmless requests an agent would see
  JailbreakBench      100 harmful and 100 benign behaviours (the benign ones are matched look-alikes)
  AgentHarm           176 + 32 harmful tasks written for tool-using agents

For harmless prompts it reports how many were allowed, held for a person, and blocked. For harmful ones, how many were
missed (allowed), held, and blocked. Each prompt is judged in a fresh session (no earned-autonomy effects) through the same
/api/evaluate the app uses. Every miss is listed in tests/results/external_report_<tag>.md. Results are cached per prompt, so a rerun is quick.

Needs: the server running (./start.sh), and  pip install datasets  (the datasets download once; log in with `hf auth login` for XSTest).
Run:   python3 tests/eval_external.py --tag wildguard [--sample 300] [--fresh] [--online] [--heldout]    (the report names the safety model that was active)"""
import hashlib, json, os, random, sys, time, urllib.request
if '--online' not in sys.argv:
    os.environ.setdefault('HF_HUB_OFFLINE', '1'); os.environ.setdefault('HF_DATASETS_OFFLINE', '1')   # use the copies already downloaded (much faster); add --online for the first download
HERE = os.path.dirname(os.path.abspath(__file__)); RES = os.path.join(HERE, 'results'); EXT = os.path.join(HERE, 'external')
os.makedirs(RES, exist_ok=True); os.makedirs(EXT, exist_ok=True)
B = 'http://127.0.0.1:8765'
SAMPLE = int(sys.argv[sys.argv.index('--sample') + 1]) if '--sample' in sys.argv else 300
TAG = sys.argv[sys.argv.index('--tag') + 1] if '--tag' in sys.argv else 'run'
CACHE = os.path.join(RES, f'external_cache_{TAG}.json')
cache = {} if '--fresh' in sys.argv or not os.path.exists(CACHE) else json.load(open(CACHE))


def post(path, data, sid=None):
    req = urllib.request.Request(B + path, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', **({'X-VerifAI-Session': sid} if sid else {})})
    return json.load(urllib.request.urlopen(req, timeout=180))


def load_sets():
    from datasets import load_dataset
    sets = {}
    x = load_dataset('walledai/XSTest')['test']
    sets['XSTest (safe look-alikes)'] = ('safe', [r['prompt'] for r in x if r['label'] == 'safe'])
    sets['XSTest (unsafe)'] = ('harmful', [r['prompt'] for r in x if r['label'] != 'safe'])
    b = load_dataset('PolyAI/banking77')['test']
    rows = [r['text'] for r in b]; random.Random(7).shuffle(rows)
    sets[f'Banking77 (sample of {min(SAMPLE, len(rows))})'] = ('safe', rows[:SAMPLE])
    j = load_dataset('JailbreakBench/JBB-Behaviors', 'behaviors')
    sets['JailbreakBench (harmful)'] = ('harmful', [r['Goal'] for r in j['harmful']])
    sets['JailbreakBench (benign look-alikes)'] = ('safe', [r['Goal'] for r in j['benign']])
    a = load_dataset('ai-safety-institute/AgentHarm', 'harmful')
    sets['AgentHarm (harmful agent tasks)'] = ('harmful', [r['prompt'] for s in a.values() for r in s])
    return sets


def active_model(sid):
    req = urllib.request.Request(B + '/api/models', headers={'X-VerifAI-Session': sid})
    j = json.load(urllib.request.urlopen(req, timeout=30))
    return next((o['name'] for o in j['safety']['options'] if o['id'] == j['safety']['active']), j['safety']['active'])


def judge(sid, text):
    k = hashlib.sha1(text.encode()).hexdigest()
    if k in cache:
        return cache[k]
    j = post('/api/evaluate', {'text': text[:3500], 'noSession': True}, sid)
    r = {'v': j['res']['verdict'], 'trigger': j['res'].get('trigger', ''), 'reason': (j['res'].get('explanation') or {}).get('reason', '')[:160]}
    cache[k] = r
    return r


def main():
    sets = load_sets()
    if '--heldout' in sys.argv:      # only the halves that were never used for training (training/external_split.py)
        keep = {t for ts in json.load(open(os.path.join(EXT, 'heldout.json'))).values() for t in ts}
        sets = {k: (kind, [p for p in ps if p in keep]) for k, (kind, ps) in sets.items()}
    sid = post('/api/session/new', {})['sid']
    out, report, t0, done = [], [f'# External evaluation: public datasets (safety model: {active_model(sid)})\n'], time.time(), 0
    total = sum(len(v[1]) for v in sets.values())
    for name, (kind, prompts) in sets.items():
        tally = {'ALLOW': 0, 'REVIEW': 0, 'BLOCK': 0}; bad = []
        for p in prompts:
            r = judge(sid, p); tally[r['v']] += 1; done += 1
            if (kind == 'safe' and r['v'] != 'ALLOW') or (kind == 'harmful' and r['v'] == 'ALLOW'):
                bad.append((p, r))
            if done % 25 == 0:
                json.dump(cache, open(CACHE, 'w')); print(f'  {done}/{total} done, {time.time() - t0:.0f}s', flush=True)
        n = len(prompts); pc = lambda c: f'{100 * c / n:.1f}%'
        if kind == 'safe':
            line = f'{name}: n={n}  allowed {tally["ALLOW"]} ({pc(tally["ALLOW"])})  held for a person {tally["REVIEW"]} ({pc(tally["REVIEW"])})  wrongly BLOCKED {tally["BLOCK"]} ({pc(tally["BLOCK"])})'
        else:
            line = f'{name}: n={n}  stopped {tally["REVIEW"] + tally["BLOCK"]} ({pc(tally["REVIEW"] + tally["BLOCK"])})  [blocked {tally["BLOCK"]}, held {tally["REVIEW"]}]  MISSED (allowed) {tally["ALLOW"]} ({pc(tally["ALLOW"])})'
        print(line, flush=True); out.append(line)
        report.append(f'## {name}\n\n{line}\n')
        report.append(f'{"Harmless prompts that were not simply allowed" if kind == "safe" else "Harmful prompts that were allowed"} ({len(bad)}):\n')
        for p, r in bad[:80]:
            report.append(f'* [{r["v"]}] {p[:200].replace(chr(10), " ")}  —  {r["trigger"]}')
        report.append('')
    json.dump(cache, open(CACHE, 'w'))
    open(os.path.join(RES, f'external_report_{TAG}.md'), 'w').write('\n'.join(report))
    print('\nSummary\n' + '\n'.join(out) + '\n\nEvery miss: tests/results/external_report_<tag>.md')


if __name__ == '__main__':
    main()
