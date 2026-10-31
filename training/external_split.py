#!/usr/bin/env python3
"""Splits the public datasets into a TRAINING half (for the policy model) and a HELD-OUT half (only ever used to measure).

  XSTest          split by its `focus` word (kill, terminate, ...): near-identical prompts share a focus word, so splitting by word
                  stops the same template landing on both sides, and the held-out half has words the model never saw
  JailbreakBench  split by Index: a harmful behaviour and its benign look-alike share an Index, so they stay together
  AgentHarm       split by id_original: the variants of one task (with and without the hint) stay together
  Banking77       training uses a seeded sample of its TRAIN split; the held-out side is the (separate) test split sample

Writes training/data/external_train.jsonl  ({text, label}: label is `none` or `no_harmful_content`)
   and tests/external/heldout.json         (the prompts that may be used for the final numbers, per dataset)
Needs the datasets already downloaded (see eval_external.py).   python3 training/external_split.py"""
import hashlib, json, os, random, re
os.environ.setdefault('HF_HUB_OFFLINE', '1'); os.environ.setdefault('HF_DATASETS_OFFLINE', '1')
from datasets import load_dataset
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
B77_TRAIN = int(os.environ.get('B77_TRAIN', 1000))
side = lambda key: int(hashlib.sha1(str(key).encode()).hexdigest(), 16) % 2      # 0 = train, 1 = held out
strip_hint = lambda t: re.sub(r'\s*Hint:.*$', '', t, flags=re.S).strip()

train, held = [], {}
add = lambda texts, label: train.extend({'text': t, 'label': label} for t in texts)

x = load_dataset('walledai/XSTest')['test']
for kind, label, name in (('safe', 'none', 'XSTest (safe look-alikes)'), ('unsafe', 'no_harmful_content', 'XSTest (unsafe)')):
    rows = [r for r in x if (r['label'] == 'safe') == (kind == 'safe')]
    add([r['prompt'] for r in rows if side(r['focus']) == 0], label)
    held[name] = [r['prompt'] for r in rows if side(r['focus']) == 1]

j = load_dataset('JailbreakBench/JBB-Behaviors', 'behaviors')
for split, label, name in (('harmful', 'no_harmful_content', 'JailbreakBench (harmful)'), ('benign', 'none', 'JailbreakBench (benign look-alikes)')):
    add([r['Goal'] for r in j[split] if side(r['Index']) == 0], label)
    held[name] = [r['Goal'] for r in j[split] if side(r['Index']) == 1]

a = load_dataset('ai-safety-institute/AgentHarm', 'harmful')
rows = [r for s in a.values() for r in s]
tr = [r['prompt'] for r in rows if side(r['id_original']) == 0]
add(tr, 'no_harmful_content'); add([strip_hint(t) for t in tr if strip_hint(t) != t], 'no_harmful_content')
held['AgentHarm (harmful agent tasks)'] = [r['prompt'] for r in rows if side(r['id_original']) == 1]

b = load_dataset('PolyAI/banking77')
pool = [r['text'] for r in b['train']]; random.Random(11).shuffle(pool)
add(pool[:B77_TRAIN], 'none')
test = [r['text'] for r in b['test']]; random.Random(7).shuffle(test)
held['Banking77 (test split sample)'] = test[:300]                                # the same 300 as eval_external.py, never trained on

seen = {}
for r in train:
    seen[r['text']] = r
train = list(seen.values())
overlap = {t for ts in held.values() for t in ts} & {r['text'] for r in train}
for t in overlap:                                                                 # a prompt must never be both trained on and measured
    for k in held: held[k] = [p for p in held[k] if p != t]
os.makedirs(os.path.join(ROOT, 'tests', 'external'), exist_ok=True)
with open(os.path.join(HERE, 'data', 'external_train.jsonl'), 'w', encoding='utf-8') as f:
    for r in train: f.write(json.dumps(r, ensure_ascii=False) + '\n')
json.dump(held, open(os.path.join(ROOT, 'tests', 'external', 'heldout.json'), 'w'), ensure_ascii=False, indent=1)
from collections import Counter
print('training rows:', len(train), dict(Counter(r['label'] for r in train)))
print('held out:', {k: len(v) for k, v in held.items()}, '| overlaps removed from held-out:', len(overlap))
