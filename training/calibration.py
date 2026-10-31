#!/usr/bin/env python3
"""How trustworthy is the impact model's own "confidence"? Bins its predictions on the hand-written test prompts and
compares what it said with how often it was right. Writes models/model_impact_v2.calibration.json (shown in the UI).
Run:  python3 training/calibration.py"""
import json, os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # impact_text (used inside the saved pipeline)
import joblib, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
clf = joblib.load(os.path.join(ROOT, 'models', 'model_impact_v2.joblib'))
X, y = [], []
for fn in ('handwritten_test.jsonl', 'handwritten_test2.jsonl'):
    for line in open(os.path.join(HERE, 'data', fn), encoding='utf-8'):
        if line.strip():
            r = json.loads(line); X.append(r['text']); y.append(r['label'])
p = clf.predict_proba(X); pred = np.array(clf.classes_)[p.argmax(1)]; conf = p.max(1); ok = (pred == np.array(y))
edges = [0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]
bins = []
for lo, hi in zip(edges[:-1], edges[1:]):
    m = (conf >= lo) & (conf < hi)
    if m.sum():
        bins.append({'lo': round(lo, 2), 'hi': round(min(hi, 1.0), 2), 'n': int(m.sum()), 'said': round(float(conf[m].mean()), 3), 'right': round(float(ok[m].mean()), 3)})
ece = sum(b['n'] * abs(b['said'] - b['right']) for b in bins) / len(X)
out = {'n': len(X), 'accuracy': round(float(ok.mean()), 3), 'mean_confidence': round(float(conf.mean()), 3), 'ece': round(float(ece), 3), 'bins': bins}
json.dump(out, open(os.path.join(ROOT, 'models', 'model_impact_v2.calibration.json'), 'w'), indent=2)
print(json.dumps(out, indent=2))
