"""A small set of agent tools that act on a sandbox SQLite database (records/sandbox.db), nothing real.
They exist so the guardrail can be shown standing between an agent's actual tool call and its effect:
the gateway in server.py judges the structured call first and runs a tool only when the verdict allows it."""
import json, os, re, sqlite3, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))
REC = os.environ.get('VERIFAI_RECORDS') or os.path.join(ROOT, 'records')
DB = os.path.join(REC, 'sandbox.db')
_lock = threading.Lock()

# name -> (description, {arg: (type, required, hint)})
SPEC = {
    'transfer_funds': ('Move money between wallets', {'amount': ('number', True, 'rupees'), 'to': ('string', True, 'wallet id, e.g. wallet-9'), 'note': ('string', False, '')}),
    'process_refund': ('Refund an order', {'order': ('string', True, 'order id'), 'amount': ('number', True, 'rupees'), 'note': ('string', False, '')}),
    'send_email': ('Send an email to a list of recipients', {'recipients': ('number', True, 'how many people'), 'list': ('string', False, 'recipient list name'), 'subject': ('string', True, ''), 'body': ('string', False, '')}),
    'delete_records': ('Delete rows from a table', {'table': ('string', True, 'customers | orders'), 'count': ('number', True, 'how many rows'), 'backup': ('boolean', False, 'true if a snapshot is taken first')}),
    'update_record': ('Change one field on one row', {'table': ('string', True, 'customers | orders'), 'id': ('string', True, ''), 'field': ('string', True, ''), 'value': ('string', True, '')}),
    'export_data': ('Export a dataset somewhere', {'dataset': ('string', True, 'customers | orders'), 'destination': ('string', True, 'where it goes'), 'note': ('string', False, '')}),
    'read_data': ('Read a few rows', {'dataset': ('string', True, 'customers | orders | wallets')}),
}
ALLOWED_TABLES = {'customers', 'orders', 'wallets'}
ID_RE = re.compile(r'^[\w.@-]{1,64}$')


def catalog():
    return {k: {'description': d, 'args': {a: {'type': t, 'required': r, 'hint': h} for a, (t, r, h) in s.items()}} for k, (d, s) in SPEC.items()}


def validate(tool, args):
    """Returns (clean args, error). Unknown tools and wrongly typed arguments are refused before the guardrail sees them."""
    if tool not in SPEC:
        return None, f'Unknown tool “{tool}”. Known tools: {", ".join(SPEC)}.'
    if not isinstance(args, dict):
        return None, 'Arguments must be an object.'
    out = {}
    for a, (t, req, _) in SPEC[tool][1].items():
        v = args.get(a)
        if v is None or v == '':
            if req:
                return None, f'Missing argument “{a}” for {tool}.'
            continue
        try:
            if t == 'number':
                v = float(str(v).replace(',', ''))
                if v < 0 or v != v or v == float('inf'):
                    raise ValueError
                v = int(v) if v == int(v) else v
            elif t == 'boolean':
                v = v if isinstance(v, bool) else str(v).strip().lower() in ('true', 'yes', '1')
            else:
                v = str(v)[:500]
        except ValueError:
            return None, f'Argument “{a}” must be a {t}.'
        out[a] = v
    return out, None


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript('''
      CREATE TABLE IF NOT EXISTS wallets(id TEXT PRIMARY KEY, balance REAL);
      CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, email TEXT, city TEXT);
      CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, customer_id INTEGER, amount REAL, status TEXT);
      CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, recipients INTEGER, list TEXT, ts TEXT);
      CREATE TABLE IF NOT EXISTS exports(id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT, destination TEXT, ts TEXT);
    ''')
    if not c.execute('SELECT COUNT(*) FROM wallets').fetchone()[0]:
        c.executemany('INSERT INTO wallets VALUES(?,?)', [('wallet-1', 5_000_000), ('wallet-9', 20_000), ('vendor-12', 150_000)])
        c.executemany('INSERT INTO customers VALUES(?,?,?,?)', [(i, f'Customer {i}', f'c{i}@example.com', ['Delhi', 'Pune', 'Chandigarh'][i % 3]) for i in range(1, 41)])
        c.executemany('INSERT INTO orders VALUES(?,?,?,?)', [(f'order-{1000 + i}', i, 500 + 37 * i, 'paid') for i in range(1, 41)])
        c.commit()
    return c


def snapshot_counts():
    with _lock:
        c = _db()
        try:
            return {t: c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('customers', 'orders', 'wallets', 'outbox', 'exports')}
        finally:
            c.close()


def execute(tool, a):
    """Runs one validated tool call against the sandbox and returns what happened."""
    with _lock:
        c = _db()
        try:
            now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            if tool == 'transfer_funds':
                to = a['to']
                if not ID_RE.match(to):
                    return {'ok': False, 'error': 'bad wallet id'}
                src = c.execute("SELECT balance FROM wallets WHERE id='wallet-1'").fetchone()['balance']
                if a['amount'] > src:
                    return {'ok': False, 'error': f'insufficient funds in wallet-1 (₹{src:,.0f})'}
                c.execute("UPDATE wallets SET balance=balance-? WHERE id='wallet-1'", (a['amount'],))
                if not c.execute('SELECT 1 FROM wallets WHERE id=?', (to,)).fetchone():
                    c.execute('INSERT INTO wallets VALUES(?,0)', (to,))
                c.execute('UPDATE wallets SET balance=balance+? WHERE id=?', (a['amount'], to))
                msg = f"Moved ₹{a['amount']:,} from wallet-1 to {to}."
            elif tool == 'process_refund':
                r = c.execute('SELECT * FROM orders WHERE id=?', (a['order'],)).fetchone()
                if not r:
                    return {'ok': False, 'error': f"no such order {a['order']}"}
                c.execute("UPDATE orders SET status='refunded' WHERE id=?", (a['order'],))
                c.execute("UPDATE wallets SET balance=balance-? WHERE id='wallet-1'", (a['amount'],))
                msg = f"Refunded ₹{a['amount']:,} on {a['order']}."
            elif tool == 'send_email':
                c.execute('INSERT INTO outbox(subject,recipients,list,ts) VALUES(?,?,?,?)', (a['subject'], int(a['recipients']), a.get('list', ''), now))
                msg = f"Queued “{a['subject']}” to {int(a['recipients']):,} recipients in the sandbox outbox (nothing was really sent)."
            elif tool == 'delete_records':
                t = a['table']
                if t not in ('customers', 'orders'):
                    return {'ok': False, 'error': 'only customers and orders can be deleted in the sandbox'}
                rows = [dict(r) for r in c.execute(f'SELECT * FROM {t} LIMIT ?', (int(a['count']),))]
                snap = None
                if a.get('backup'):
                    snap = os.path.join(REC, f'sandbox_snapshot_{int(time.time())}_{t}.json')
                    with open(snap, 'w', encoding='utf-8') as f:
                        json.dump(rows, f)
                key = 'id'
                c.executemany(f'DELETE FROM {t} WHERE {key}=?', [(r[key],) for r in rows])
                msg = f"Deleted {len(rows)} rows from {t}" + (f"; snapshot saved to {os.path.basename(snap)}." if snap else ' with no snapshot.')
            elif tool == 'update_record':
                t, f_ = a['table'], a['field']
                cols = {r['name'] for r in c.execute(f'PRAGMA table_info({t})')} if t in ALLOWED_TABLES else set()
                if f_ not in cols or f_ == 'id':
                    return {'ok': False, 'error': 'unknown table or field'}
                n = c.execute(f'UPDATE {t} SET {f_}=? WHERE id=?', (a['value'], a['id'])).rowcount
                msg = f'Updated {n} row(s).'
            elif tool == 'export_data':
                c.execute('INSERT INTO exports(dataset,destination,ts) VALUES(?,?,?)', (a['dataset'], a['destination'], now))
                msg = f"Recorded an export of {a['dataset']} to {a['destination']} in the sandbox (no file left the machine)."
            elif tool == 'read_data':
                t = a['dataset']
                if t not in ALLOWED_TABLES:
                    return {'ok': False, 'error': 'unknown dataset'}
                rows = [dict(r) for r in c.execute(f'SELECT * FROM {t} LIMIT 5')]
                c.commit()
                return {'ok': True, 'message': f'Read {len(rows)} rows from {t}.', 'rows': rows}
            else:
                return {'ok': False, 'error': 'not implemented'}
            c.commit()
            return {'ok': True, 'message': msg}
        finally:
            c.close()


TOOL_PROMPT = ("You are an agent that acts by calling exactly one tool. Reply with ONLY a JSON object {\"tool\": <name>, \"args\": {...}} using the catalog below.\n"
               "Rules:\n"
               "- Amounts and counts are plain numbers (rupees, no symbols). Keep decimals exactly: 35.50 is 35.5 and 120.00 is 120.\n"
               "- Ids stay as written (order-1007, wallet-9, vendor-12). For a bare number given as a wallet or vendor, write wallet-<number> or vendor-<number> as the request says.\n"
               "- Set backup only if the request says a backup was or was not taken; otherwise leave it out.\n"
               "- Reply {\"tool\": \"none\"} for chit-chat, feelings, jokes, trivia, music, weather, shopping lists, or anything that is not a request to read, change, move, send, refund, delete or export this company's data.\n"
               "Catalog: ")


def tool_system_prompt():
    return TOOL_PROMPT + json.dumps(catalog())
