#!/usr/bin/env python3
"""Trains the impact model (LOW / MID / HIGH) on data/generated.jsonl and checks it on the separate,
hand-written data/handwritten_test.jsonl. Saves models/model_impact_v2.joblib and a metrics file.

    python3 train_impact.py

Honest scope: the labels come from the class definitions in generate_data.py, so the model learns THIS
project's idea of impact. The hand-written test set is small (83 prompts) and was written by one person.
"""
import json, os, random, re, subprocess, sys, time
import numpy as np
import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import FunctionTransformer

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)
from impact_text import prep_all
LABELS = ['LOW', 'MID', 'HIGH']
load = lambda p: [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
gen = load(os.path.join(HERE, 'data', 'generated.jsonl'))
fp = os.path.join(HERE, 'data', 'generated_focus.jsonl')
if os.path.exists(fp): gen += load(fp)
tests = {'test1': load(os.path.join(HERE, 'data', 'handwritten_test.jsonl')), 'test2': load(os.path.join(HERE, 'data', 'handwritten_test2.jsonl'))}
test = tests['test1'] + tests['test2']
norm = lambda s: re.sub(r'\s+', ' ', s.lower()).strip()
test_texts = {norm(t['text']) for t in test}
rows = [r for r in gen if norm(r['text']) not in test_texts]      # never train on a test prompt
seen = set(); train = []
for r in rows:
    if norm(r['text']) not in seen: seen.add(norm(r['text'])); train.append(r)
print(f'training rows: {len(train)} (dropped {len(gen) - len(train)} duplicates/test overlaps)')
print('  per label:', {l: sum(1 for r in train if r['label'] == l) for l in LABELS})

rng = random.Random(11)
def typo(s):
    ws = s.split(); idx = [i for i, w in enumerate(ws) if len(w) >= 5]
    if not idx: return s
    i = rng.choice(idx); w = ws[i]; k = rng.randrange(1, len(w) - 1)
    op = rng.choice(['swap', 'drop', 'dup'])
    ws[i] = (w[:k] + w[k + 1] + w[k] + w[k + 2:]) if op == 'swap' and k + 2 <= len(w) else (w[:k] + w[k + 1:] if op == 'drop' else w[:k] + w[k] + w[k:])
    return ' '.join(ws)
def augment(rs):
    out = list(rs)
    for r in rs:
        if rng.random() < 0.30: out.append({**r, 'text': typo(r['text'])})
        if rng.random() < 0.10: out.append({**r, 'text': r['text'].lower()})
    return out

def pipe(C):
    feats = FeatureUnion([
        ('word', TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=2, lowercase=True)),
        ('char', TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 5), sublinear_tf=True, min_df=3, lowercase=True)),
    ])
    return Pipeline([('prep', FunctionTransformer(prep_all, validate=False)), ('f', feats), ('clf', LogisticRegression(C=C, max_iter=3000, class_weight='balanced'))])

X = [r['text'] for r in train]; y = [r['label'] for r in train]

# choose C by 5-fold cross-validation on the generated data
best = None
for C in [1, 3, 10, 30]:
    pred = cross_val_predict(pipe(C), X, y, cv=StratifiedKFold(5, shuffle=True, random_state=1), n_jobs=-1)
    f1 = f1_score(y, pred, average='macro'); print(f'  C={C:<3} 5-fold macro-F1 on generated data: {f1:.3f}')
    if best is None or f1 > best[0]: best = (f1, C)
C = best[1]; print('chosen C =', C)

aug = augment(train)
model = pipe(C).fit([r['text'] for r in aug], [r['label'] for r in aug])
res = {'n_train': len(train), 'n_train_augmented': len(aug), 'C': C, 'cv_macro_f1': round(best[0], 4)}
try:
    sys.path.insert(0, ROOT); import server as S
    S._load_models(); S._model_state['impact_v2'] = None            # the ORIGINAL model, not the one being trained
    old_pred = lambda xs: [S._predict_intent_impact(t, t)['impact'] for t in xs]
except Exception as e:
    old_pred = None; print('old model comparison skipped:', e)
def heur_pred(xs):
    js = ("global.window=global;require(process.argv[1]+'/engine.js');const V=VerifAI;const T=JSON.parse(require('fs').readFileSync(0,'utf8'));"
          "const c={agent:'a',status:'ACTIVE',snapshot:true};console.log(JSON.stringify(T.map(t=>{const a=V.fromPrompt(t,c);const r=V.evaluate(a,V.DEFAULT_CFG,[]);return r.imp.score;})))")
    out = subprocess.run(['node', '-e', js, ROOT], input=json.dumps(xs), capture_output=True, text=True, timeout=60).stdout
    return ['LOW' if v < 3.25 else 'MID' if v < 6.75 else 'HIGH' for v in json.loads(out)]
for name, ts in tests.items():
    Xt = [r['text'] for r in ts]; yt = [r['label'] for r in ts]
    proba = model.predict_proba(Xt); pred = model.classes_[proba.argmax(1)]; conf = proba.max(1)
    acc = accuracy_score(yt, pred); mf1 = f1_score(yt, pred, average='macro', labels=LABELS)
    print(f'\n=== {name}: {len(yt)} hand-written prompts ===  NEW model accuracy {acc:.1%}  (macro-F1 {mf1:.3f})')
    cm = confusion_matrix(yt, pred, labels=LABELS); print('confusion (rows = true, cols = predicted):')
    for l, r in zip(LABELS, cm): print(f'   {l:>5}  ' + '  '.join(f'{v:>3}' for v in r))
    hi = conf >= 0.6
    if hi.sum(): print(f'  confidence >= 0.60 on {hi.sum()}/{len(yt)}; accuracy on those {accuracy_score(np.array(yt)[hi], pred[hi]):.1%}')
    res[f'{name}_conf60_accuracy'] = round(float(accuracy_score(np.array(yt)[hi], pred[hi])), 4) if hi.sum() else None
    res[f'{name}_new_accuracy'] = round(acc, 4); res[f'{name}_new_macro_f1'] = round(mf1, 4); res[f'{name}_n'] = len(yt)
    if old_pred: oa = accuracy_score(yt, old_pred(Xt)); print(f'  ORIGINAL model: {oa:.1%}'); res[f'{name}_old_accuracy'] = round(oa, 4)
    try: ha = accuracy_score(yt, heur_pred(Xt)); print(f'  rule-based heuristic: {ha:.1%}'); res[f'{name}_heuristic_accuracy'] = round(ha, 4)
    except Exception as e: print('  heuristic skipped:', e)
    print('  wrong:'); [print(f'    want {t:4} got {p:4} ({c:.2f})  {x[:66]}') for x, t, p, c in zip(Xt, yt, pred, conf) if t != p]

out_path = os.path.join(ROOT, 'models', 'model_impact_v2.joblib')
joblib.dump(model, out_path, compress=3)
res['trained_at'] = time.strftime('%Y-%m-%d %H:%M'); res['labels'] = [str(c) for c in model.classes_]
json.dump(res, open(os.path.join(ROOT, 'models', 'model_impact_v2.metrics.json'), 'w'), indent=2)
print('\nsaved', out_path, os.path.getsize(out_path) // 1024, 'KB')
