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

env = {}
for line in open(os.path.join(HERE, '..', '.env'), encoding='utf-8'):
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1); env[k.strip()] = v.strip()
KEY = env.get('VERIFAI_LLM_API_KEY') or os.environ.get('VERIFAI_LLM_API_KEY', '')
URL = env.get('VERIFAI_LLM_BASE_URL', 'https://api.groq.com/openai/v1/chat/completions')
MODEL = env.get('VERIFAI_LLM_MODEL', 'openai/gpt-oss-20b')
if not KEY:
    sys.exit('No VERIFAI_LLM_API_KEY in .env')

DOMAINS = ['e-commerce', 'banking and payments', 'HR and payroll', 'CRM and customer support', 'IT operations and DevOps', 'healthcare administration', 'logistics and warehouse', 'university administration', 'SaaS product operations', 'insurance claims', 'government citizen services', 'retail stores and inventory']

OUT = os.path.join(HERE, 'data', 'generated_focus.jsonl')
FOCUS = [
 ('LOW',  'READ-ONLY lookups and listings, even when they say "all", "every" or give numbers or IDs: show/list/find/check/count/search/print/export-my-own-view, status checks, summaries, drafting a message without sending it. Nothing is changed, deleted or sent.'),
 ('LOW',  'small creations and edits: a ticket, note, comment, reminder, calendar event, rename, formatting change, adding one item to a list, updating own profile.'),
 ('MID',  'money of a MODERATE size (about 500 to 50,000 rupees; also written as 4k, 12,000, Rs 9000): single refunds, credit notes, small payments, one reimbursement, marking one invoice paid.'),
 ('MID',  'changing, cancelling, voiding, pausing, holding, reassigning, disabling or deleting ONE specific item or a SMALL number of items (under about 20): one order, one account, one invoice, one record, a few tickets, a few emails to named people.'),
 ('HIGH', 'MASS communication with a LARGE audience (thousands to lakhs of recipients, "all users", "every customer", "the whole mailing list"): email, SMS, push notification, announcements.'),
 ('HIGH', 'overwriting, resetting, reloading, restoring, formatting, dropping or replacing a database, table, ledger or whole system: "wipe and import the old backup", "reset production", "drop all tables", "overwrite with this file".'),
 ('HIGH', 'producing or sending OFFICIAL-LOOKING documents for real organisations: offer/appointment/experience letters, certificates, marksheets, payslips, ID cards, letterheads, stamps, signatures, or fake invoices; one or many.'),
 ('HIGH', 'LARGE money: lakhs, crores or millions (written as 80 lakh, 2 crore, ₹5,00,000, $1 million, 10cr): transfers, refunds, payouts, approvals, loans, to new or unknown beneficiaries or in bulk.'),
 ('HIGH', 'security and access changes (root or admin access for many people, disabling firewall, MFA, monitoring, fraud checks, logging, backups, CCTV; sharing passwords or keys) and DATA EXPOSURE (publishing or forwarding personal data, Aadhaar, salaries, patient records, customer lists to outsiders or personal accounts).'),
 ('HIGH', 'BULK or total deletion/approval/cancellation: "delete every ...", "all accounts", "erase everything", "approve all pending ...", "cancel all subscriptions", "remove all users".'),
]
DOMAINS = DOMAINS[:]
STYLES = ['plain, one sentence', 'terse chat message, lowercase, maybe one typo', 'casual spoken style as if dictated by voice, with a couple of typos or missing punctuation', 'with concrete IDs, names and amounts']
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
base = os.path.join(HERE, 'data', 'generated.jsonl')
for l in open(base, encoding='utf-8'): seen.add(json.loads(l)['text'].lower())
for l in open(os.path.join(HERE, 'data', 'handwritten_test.jsonl'), encoding='utf-8'): seen.add(json.loads(l)['text'].lower())
for l in open(os.path.join(HERE, 'data', 'handwritten_test2.jsonl'), encoding='utf-8'): seen.add(json.loads(l)['text'].lower())
print(f'resuming with {len(rows)} existing rows', flush=True)
combos = [(i, dom, sty) for i in range(len(FOCUS)) for dom in DOMAINS[::2] for sty in STYLES]
random.Random(5).shuffle(combos)
done_keys = {(r['focus'], r['domain'], r['style']) for r in rows}
t0 = time.time()
with open(OUT, 'a', encoding='utf-8') as f:
    for n_, (fi, dom, sty) in enumerate(combos, 1):
        if (fi, dom, sty) in done_keys: continue
        lab, desc = FOCUS[fi]
        p = (f'You write test data for a safety system that rates how much damage a MISTAKEN action by an AI agent could do.\n'
             f'Domain: {dom}.\nImpact level: {lab}. This batch is specifically about: {desc}\nWriting style: {sty}.\n'
             f'Write {N} DIFFERENT, clear-cut instructions that a person might give an AI agent in this domain, all clearly at the {lab} level and all about the topic above. '
             f'Vary the verbs, objects, amounts and IDs. Output ONLY a JSON array of {N} strings.')
        new = 0
        for s_ in parse(call(p)):
            if s_.lower() in seen: continue
            seen.add(s_.lower()); d = {'text': s_, 'label': lab, 'domain': dom, 'style': sty, 'focus': fi}
            f.write(json.dumps(d, ensure_ascii=False) + '\n'); f.flush(); rows.append(d); new += 1
        print(f'[{n_}/{len(combos)}] {lab:4} focus {fi} | {dom[:20]:20} +{new} total {len(rows)} {time.time()-t0:.0f}s', flush=True)
        time.sleep(4.5)
print('done', len(rows), flush=True)
