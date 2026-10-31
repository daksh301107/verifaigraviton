#!/usr/bin/env python3
"""Checks the policy-rule model on hand-written paraphrases it never trained on.
Run: python3 tests/test_policy_model.py"""
import json, os, sys
import joblib, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
clf = joblib.load(os.path.join(ROOT, 'models', 'model_policy_intent.joblib'))
held = json.load(open(os.path.join(ROOT, 'tests', 'hard_policy_paraphrases.json'), encoding='utf-8'))
ok = held.pop('ok'); held.pop('note', None)
cl = [str(c) for c in clf.classes_]; ni = cl.index('none')
def score(t):
    p = clf.predict_proba([t])[0]; q = p.copy(); q[ni] = 0
    return cl[int(q.argmax())], 1 - float(p[ni])
REVIEW, WG = 0.5, 0.6          # engine defaults: a person reviews from REVIEW; with WildGuard the model blocks from WG
total = sum(len(v) for v in held.values())
right = sum(1 for r, ts in held.items() for t in ts if score(t)[0] == r and score(t)[1] >= REVIEW)
flagged = sum(1 for r, ts in held.items() for t in ts if score(t)[1] >= REVIEW)
fp = [t for t in ok if score(t)[1] >= REVIEW]
bad = 0
def check(name, cond, extra=''):
    global bad; bad += not cond; print(('ok   ' if cond else 'FAIL ') + name + (' ' + extra if extra else ''))
check(f'at least 85% of unseen paraphrases reach a person or a block ({flagged}/{total})', flagged / total >= 0.85)
check(f'at least 80% are matched to the right rule ({right}/{total})', right / total >= 0.80)
check(f'at most 3 of {len(ok)} benign look-alikes are flagged', len(fp) <= 3, str(fp))
sys.exit(1 if bad else 0)
