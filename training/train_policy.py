#!/usr/bin/env python3
"""Trains the policy-intent model: which hard-policy rule (if any) does a request describe?
Output: models/model_policy_intent.joblib + .metrics.json.
Evaluation is on tests/hard_policy_paraphrases.json, hand-written and never used for training.
Run:  python3 training/train_policy.py"""
import json, os, sys
import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline, make_union
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)

def load():
    X, y = [], []
    for line in open(os.path.join(HERE, 'data', 'policy_intent.jsonl'), encoding='utf-8'):
        r = json.loads(line); X.append(r['text']); y.append(r['label'])
    # LLM-written paraphrases and same-topic benign look-alikes (training/generate_policy_llm.py)
    fp = os.path.join(HERE, 'data', 'policy_llm.jsonl')
    if os.path.exists(fp):
        for line in open(fp, encoding='utf-8'):
            r = json.loads(line); X.append(r['text']); y.append(r['label'])
    # public datasets, TRAINING halves only (training/external_split.py): real harmless and harmful requests
    fe = os.path.join(HERE, 'data', 'external_train.jsonl')
    if os.path.exists(fe):
        for line in open(fe, encoding='utf-8'):
            r = json.loads(line); X.append(r['text']); y.append(r['label'])
    return X, y

def build():
    return make_pipeline(
        make_union(TfidfVectorizer(ngram_range=(1, 3), sublinear_tf=True, lowercase=True, min_df=1),
                   TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 5), sublinear_tf=True, lowercase=True, min_df=2)),
        LogisticRegression(C=30, max_iter=3000, class_weight='balanced'))

def main():
    X, y = load()
    clf = build().fit(X, y)
    held = json.load(open(os.path.join(ROOT, 'tests', 'hard_policy_paraphrases.json'), encoding='utf-8'))
    ok = held.pop('ok'); held.pop('note', None)
    classes = list(clf.classes_)
    ni = classes.index('none')
    def top(t):
        # score = probability that the request breaks SOME rule; the rule is the likeliest non-"none" class
        p = clf.predict_proba([t])[0]; q = p.copy(); q[ni] = 0
        return classes[int(np.argmax(q))], float(1 - p[ni])
    res = {}
    for thr in (0.4, 0.5, 0.6, 0.7, 0.85):
        caught = sum(1 for r, ts in held.items() for t in ts if top(t)[0] == r and top(t)[1] >= thr)
        flagged = sum(1 for r, ts in held.items() for t in ts if top(t)[1] >= thr)
        total = sum(len(ts) for ts in held.values())
        fp = sum(1 for t in ok if top(t)[0] != 'none' and top(t)[1] >= thr)
        res[str(thr)] = {'recall': round(caught / total, 3), 'caught': caught, 'flagged_any_rule': flagged, 'total': total, 'false_positives': fp, 'benign': len(ok)}
    # training-set sanity (cross-checked by the held-out numbers above, not a measure of accuracy)
    out = {'trained_on': len(X), 'rules': len(classes) - 1, 'held_out': res}
    joblib.dump(clf, os.path.join(ROOT, 'models', 'model_policy_intent.joblib'))
    json.dump(out, open(os.path.join(ROOT, 'models', 'model_policy_intent.metrics.json'), 'w'), indent=2)
    print(json.dumps(out, indent=2))
    for r, ts in held.items():
        for t in ts:
            lab, p = top(t)
            if lab != r or p < 0.6: print(f'  miss  {r:26s} -> {lab} ({p:.2f}) | {t}')
    for t in ok:
        lab, p = top(t)
        if p >= 0.5: print(f'  FALSE+ {lab} ({p:.2f}) | {t}')
if __name__ == '__main__': main()
