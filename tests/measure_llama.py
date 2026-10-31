#!/usr/bin/env python3
"""How well does Llama 3.1 8B pick the right tool and fill in its arguments? Uses the same instruction the server gives it
(server._TOOL_SYS + the tool catalog) on 24 requests written by us, and scores the answer against what a person would pick.
Needs llama_local.py running (python3 llama_local.py).   python3 tests/measure_llama.py"""
import json, os, re, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import vf_tools
URL = os.environ.get('LLAMA_URL', 'http://127.0.0.1:8001/v1/chat/completions')
SYS = vf_tools.tool_system_prompt()   # exactly what the server sends
# (request, expected tool, expected arguments that must be right)
CASES = [
 ('Pay vendor-12 5000 rupees', 'transfer_funds', {'amount': 5000, 'to': 'vendor-12'}),
 ('Send 2500 to wallet-9', 'transfer_funds', {'amount': 2500, 'to': 'wallet-9'}),
 ('Move 20,000 rupees into wallet-1', 'transfer_funds', {'amount': 20000, 'to': 'wallet-1'}),
 ('Transfer fifty thousand to vendor-12', 'transfer_funds', {'amount': 50000, 'to': 'vendor-12'}),
 ('Refund order-1005 for 800 rupees', 'process_refund', {'order': 'order-1005', 'amount': 800}),
 ('Refund 1200 on order-1010', 'process_refund', {'order': 'order-1010', 'amount': 1200}),
 ('Refund order-1020 fully, it was 640 rupees', 'process_refund', {'order': 'order-1020', 'amount': 640}),
 ('Email our 300 subscribers the monthly update', 'send_email', {'recipients': 300}),
 ('Email all 1,500 members about the offer', 'send_email', {'recipients': 1500}),
 ('Send a thank-you email to one customer', 'send_email', {'recipients': 1}),
 ('Delete 3 old orders, take a backup first', 'delete_records', {'table': 'orders', 'count': 3, 'backup': True}),
 ('Remove 10 customers without a backup', 'delete_records', {'table': 'customers', 'count': 10, 'backup': False}),
 ('Delete the 5 oldest orders, a backup has been taken', 'delete_records', {'table': 'orders', 'count': 5, 'backup': True}),
 ('Change the city of customer 7 to Pune', 'update_record', {'table': 'customers', 'id': '7', 'field': 'city', 'value': 'pune'}),
 ('Set order-1003 status to shipped', 'update_record', {'table': 'orders', 'id': 'order-1003', 'field': 'status', 'value': 'shipped'}),
 ("Update customer 12's email to a@b.com", 'update_record', {'table': 'customers', 'id': '12', 'field': 'email', 'value': 'a@b.com'}),
 ("Export the customers table to the auditor's drive", 'export_data', {'dataset': 'customers'}),
 ('Export orders to our partner', 'export_data', {'dataset': 'orders'}),
 ('Show me the wallet balances', 'read_data', {'dataset': 'wallets'}),
 ('What are the first few customers?', 'read_data', {'dataset': 'customers'}),
 ('List some orders', 'read_data', {'dataset': 'orders'}),
 ('Tell me a joke', 'none', {}),
 ("What's the weather in Delhi?", 'none', {}),
 ("Cancel Friday's lunch plans", 'none', {}),
]
def ask(goal):
    body = json.dumps({'model': 'llama-3.1-8b-instruct', 'messages': [{'role': 'system', 'content': SYS}, {'role': 'user', 'content': goal}], 'max_tokens': 200, 'temperature': 0}).encode()
    req = urllib.request.Request(URL, data=body, headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=120))['choices'][0]['message']['content']
def same(got, want):
    g = str(got).strip().lower().replace(',', '')
    if isinstance(want, bool): return got is want or g == str(want).lower()
    if isinstance(want, (int, float)):
        try: return float(g) == float(want)
        except ValueError: return False
    return g == str(want).lower()
rows, t0 = [], time.time()
for goal, tool, want in CASES:
    out = ask(goal); m = re.search(r'\{.*\}', out, re.S)
    try: call = json.loads(m.group(0)) if m else None
    except ValueError: call = None
    parsed = isinstance(call, dict)
    got_tool = (call or {}).get('tool') if parsed else None
    if got_tool in (None, '', 'none'): got_tool = 'none' if parsed else None
    tool_ok = got_tool == tool
    args = (call or {}).get('args') or {}
    clean, err = (vf_tools.validate(got_tool, args) if tool_ok and tool != 'none' else (None, None))
    valid = tool != 'none' and tool_ok and err is None
    args_ok = tool_ok and all(same(args.get(k), v) for k, v in want.items())
    rows.append({'goal': goal, 'expected': tool, 'got': got_tool, 'json_ok': parsed, 'tool_ok': tool_ok, 'args_valid': valid if tool != 'none' else None, 'args_ok': args_ok, 'raw': out[:160]})
    print(('ok   ' if tool_ok and args_ok else 'MISS ') + f'{goal[:52]:52} -> {got_tool}  {json.dumps(args)[:70] if tool_ok and not args_ok else ""}', flush=True)
n = len(rows); acts = [r for r in rows if r['expected'] != 'none']; nones = [r for r in rows if r['expected'] == 'none']
res = {'n': n, 'json_valid': sum(r['json_ok'] for r in rows), 'right_tool': sum(r['tool_ok'] for r in rows), 'right_tool_and_arguments': sum(r['tool_ok'] and r['args_ok'] for r in rows),
       'actions': len(acts), 'actions_right_tool': sum(r['tool_ok'] for r in acts), 'actions_all_args_right': sum(r['tool_ok'] and r['args_ok'] for r in acts),
       'no_tool_cases': len(nones), 'no_tool_right': sum(r['tool_ok'] for r in nones), 'seconds_per_request': round((time.time() - t0) / n, 1), 'items': rows}
json.dump(res, open(os.path.join(HERE, 'results', 'llama_tools.json'), 'w'), indent=2)
print(f"\nvalid JSON {res['json_valid']}/{n} | right tool {res['right_tool']}/{n} | right tool AND every argument right {res['right_tool_and_arguments']}/{n}")
print(f"actions: right tool {res['actions_right_tool']}/{res['actions']}, all arguments right {res['actions_all_args_right']}/{res['actions']} | should-not-act cases handled {res['no_tool_right']}/{res['no_tool_cases']} | {res['seconds_per_request']} s per request")
