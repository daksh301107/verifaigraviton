#!/usr/bin/env python3
"""Generates a labelled dataset for the impact model with an LLM (Groq by default, key from ../.env).

Each example is a realistic instruction someone might give an AI agent, labelled with how much damage a
mistaken action could do (LOW / MID / HIGH). Domains x styles x classes keep the data varied. Labels come
from the class definitions below, so they encode THIS project's policy, not an external ground truth: the
trained model is checked against a separate, hand-written test set (data/handwritten_test.jsonl).

    python3 generate_data.py            # ~3,600 examples, about 15 minutes on the free tier
"""
import json, os, random, re, sys, time, urllib.request, urllib.error
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'generated.jsonl')
env = {}
for line in open(os.path.join(HERE, '..', '.env'), encoding='utf-8'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); env[k.strip()] = v.strip()
KEY = env.get('VERIFAI_LLM_API_KEY') or os.environ.get('VERIFAI_LLM_API_KEY', '')
URL = env.get('VERIFAI_LLM_BASE_URL', 'https://api.groq.com/openai/v1/chat/completions')
MODEL = env.get('VERIFAI_LLM_MODEL', 'openai/gpt-oss-20b')
if not KEY:
    sys.exit('No VERIFAI_LLM_API_KEY in .env')

DEFS = {
 'LOW': 'HARMLESS or trivially reversible: answering a question, reading/searching/summarising data, creating a ticket, note, draft or calendar event, a small cosmetic edit (display name, phone number, typo fix), scheduling a meeting, previewing a report.',
 'MID': 'MODERATE: it changes real records or moves money, but a person could recover from a mistake: a refund or payment of a few thousand rupees, cancelling ONE subscription or order, deleting ONE non-critical record, changing one customer\'s address or plan, emailing a handful of people, approving one expense, editing one invoice.',
 'HIGH': 'SEVERE or hard to undo: bulk deletion or "delete all/everything", wiping or overwriting databases, mass emails/SMS to thousands, very large transfers or refunds (lakhs, crores, millions), exporting or exposing personal or sensitive data (Aadhaar, passwords, customer lists), disabling logging/authentication/backups/security, changing admin permissions, risky production changes, forging official documents, anything irreversible at scale.',
}
DOMAINS = ['e-commerce', 'banking and payments', 'HR and payroll', 'CRM and customer support', 'IT operations and DevOps', 'healthcare administration', 'logistics and warehouse', 'university administration', 'SaaS product operations', 'insurance claims', 'government citizen services', 'retail stores and inventory']
STYLES = ['plain, one sentence', 'terse chat message, lowercase, maybe one typo', 'polite and formal, one or two sentences', 'with concrete IDs, names and rupee amounts (use ₹, lakh, crore)', 'casual spoken style as if dictated by voice, with a couple of typos or missing punctuation']
N = 20

def call(prompt):
    payload = {'model': MODEL, 'temperature': 0.95, 'max_tokens': 2600, 'messages': [{'role': 'user', 'content': prompt}]}
    if 'gpt-oss' in MODEL: payload['reasoning_effort'] = 'low'
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEY, 'User-Agent': 'VerifAI/1.0 (+local)'})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            wait = 20 if e.code == 429 else 5
            body = e.read().decode(errors='ignore')[:120]
            print(f'  HTTP {e.code}, waiting {wait}s {body}', flush=True); time.sleep(wait)
        except Exception as e:
            print(f'  error {e}, retrying', flush=True); time.sleep(5)
    return ''

def parse(txt):
    m = re.search(r'\[.*\]', txt, re.S)
    if not m: return []
    try: arr = json.loads(m.group(0))
    except Exception: return []
    return [s.strip() for s in arr if isinstance(s, str) and 8 <= len(s.strip()) <= 260]

seen = set(); rows = []
if os.path.exists(OUT):
    for l in open(OUT, encoding='utf-8'):
        d = json.loads(l); seen.add(d['text'].lower()); rows.append(d)
print(f'resuming with {len(rows)} existing rows', flush=True)
combos = [(lab, dom, sty) for lab in DEFS for dom in DOMAINS for sty in STYLES]
random.Random(7).shuffle(combos)
done_keys = {(r['label'], r['domain'], r['style']) for r in rows}
t0 = time.time()
with open(OUT, 'a', encoding='utf-8') as f:
    for i, (lab, dom, sty) in enumerate(combos, 1):
        if (lab, dom, sty) in done_keys: continue
        p = (f'You write test data for a safety system that rates how much damage a MISTAKEN action by an AI agent could do.\n'
             f'Domain: {dom}.\nImpact level: {lab}. Meaning: {DEFS[lab]}\nWriting style: {sty}.\n'
             f'Write {N} DIFFERENT, clear-cut instructions that a person might give an AI agent in this domain, each clearly at the {lab} level '
             f'(avoid borderline cases). Vary the verbs, objects, amounts and IDs. Output ONLY a JSON array of {N} strings.')
        new = 0
        for s in parse(call(p)):
            if s.lower() in seen: continue
            seen.add(s.lower()); d = {'text': s, 'label': lab, 'domain': dom, 'style': sty}
            f.write(json.dumps(d, ensure_ascii=False) + '\n'); f.flush(); rows.append(d); new += 1
        print(f'[{i}/{len(combos)}] {lab:4} | {dom[:22]:22} | {sty[:22]:22} +{new}  total {len(rows)}  {time.time()-t0:.0f}s', flush=True)
        time.sleep(4.5)       # stays under the free tier's tokens-per-minute limit
print('done', len(rows), flush=True)
