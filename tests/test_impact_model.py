#!/usr/bin/env python3
"""Impact-model regression test.   Run:  python3 tests/test_impact_model.py
Fails if the trained model is missing, or its accuracy on the two hand-written test sets drops below the floors."""
import json, os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT)
import joblib
from sklearn.metrics import accuracy_score

FLOORS = {'handwritten_test': 0.88, 'handwritten_test2': 0.88}      # measured: 92.8% and 96.7%
model = joblib.load(os.path.join(ROOT, 'models', 'model_impact_v2.joblib'))
bad = 0
for name, floor in FLOORS.items():
    rows = [json.loads(l) for l in open(os.path.join(ROOT, 'training', 'data', name + '.jsonl'), encoding='utf-8') if l.strip()]
    acc = accuracy_score([r['label'] for r in rows], model.predict([r['text'] for r in rows]))
    ok = acc >= floor; bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {acc:.1%} on {len(rows)} prompts (floor {floor:.0%})")
# typo and size-of-amount behaviour the feature work was meant to fix
checks = [('Transfer ₹5000 to vendor-102', 'MID'), ('Move ₹80 lakh to a new beneficiary account', 'HIGH'), ('Message all 50,000 app users that their accounts were hacked', 'HIGH'),
          ('Send a reminder email to 5 customers', 'MID'), ('delte all the comapny recored', 'HIGH'), ('What is the status of order 5521?', 'LOW')]
for text, want in checks:
    got = str(model.predict([text])[0]); ok = got == want; bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {text!r} -> {got} (want {want})")
sys.exit(1 if bad else 0)
