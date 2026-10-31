"""Server-side evidence ledger.

The browser no longer writes the ledger: the server builds every row from decisions it made itself, chains it to
the previous row (SHA-256 over previous hash + the row's fields, the same scheme the old browser code used), signs
the hash with HMAC-SHA256, and appends a checkpoint to a separate anchor log. The key and the anchor log live in
~/.verifai/, outside the project and records folders, so copying or zipping the project does not carry the key and
editing records/ alone cannot produce a valid signature.

Honest limit: someone who controls this Mac account (and so the key) can still rewrite history. Anchoring the
latest hash somewhere you do not control (VERIFAI_ANCHOR_URL, or pasting the hash into an email or a ticket) is
what closes that gap; see SECURITY.md.
"""
import hashlib, hmac, json, os, re, secrets, threading, time, urllib.request

HOME = os.path.expanduser(os.environ.get('VERIFAI_HOME', '~/.verifai'))
FIELDS_V1 = ['seq', 'ts', 'agent', 'action', 'verdict', 'tier', 'score', 'trigger']
FIELDS_V2 = FIELDS_V1 + ['actor', 'decision_id', 'v']

_PII = [(re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gsk_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,})\b'), '[api key]'),
        (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[ssn]'),
        (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '[email]'),
        (re.compile(r'\b\d{4}\s\d{4}\s\d{4}\b'), '[aadhaar]'),
        (re.compile(r'\b[A-Z]{5}\d{4}[A-Z]\b'), '[pan]'),
        (re.compile(r'\b(?:\d{4}[ -]){3}\d{4}\b'), '[card]'),
        (re.compile(r'(?:\+\d{1,3}[-\s]?)?\b\d{5}[-\s]?\d{5}\b'), '[phone]')]


def mask_pii(text):
    """Personal identifiers are replaced before anything is written to disk (set VERIFAI_STORE_RAW=1 to keep them)."""
    t = str(text)
    if os.environ.get('VERIFAI_STORE_RAW') == '1':
        return t
    for rx, tag in _PII:
        t = rx.sub(tag, t)
    return t


def _fnv(s):
    h = 0x811c9dc5
    b = s.encode('utf-16-le')
    for i in range(0, len(b), 2):
        h ^= b[i] | (b[i + 1] << 8)
        h = (h * 0x01000193) & 0xffffffff
    return f'{h:08x}'


def canon(row, fields):
    """Exactly what JSON.stringify gives for the same object in the browser."""
    return json.dumps({k: row.get(k) for k in fields}, ensure_ascii=False, separators=(',', ':'))


def _private_dir(path):
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


class Ledger:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        _private_dir(HOME)
        self.key_path = os.path.join(HOME, 'ledger.key')
        self.anchor_path = os.path.join(HOME, 'anchors.log')
        if not os.path.exists(self.key_path):
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_hex(32))
        with open(self.key_path) as f:
            self.key = bytes.fromhex(f.read().strip())
        self.anchor_url = os.environ.get('VERIFAI_ANCHOR_URL', '')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.chmod(path, 0o600)

    def rows(self):
        out = []
        try:
            with open(self.path, encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        out.append(json.loads(line))
        except FileNotFoundError:
            pass
        return out

    def last(self):
        r = self.rows()
        return r[-1] if r else None

    def append(self, agent, action, verdict, tier, score, trigger, actor='system', decision_id=''):
        with self.lock:
            last = self.last()
            seq = (last['seq'] + 1) if last else 1
            prev = last['hash'] if last else '00000000'
            row = {'seq': seq, 'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + f'.{int(time.time() * 1000) % 1000:03d}Z',
                   'agent': agent, 'action': mask_pii(action), 'verdict': verdict, 'tier': tier, 'score': score, 'trigger': trigger,
                   'actor': actor, 'decision_id': decision_id, 'v': 2}
            h = hashlib.sha256((prev + canon(row, FIELDS_V2)).encode('utf-8')).hexdigest()
            row.update({'prev': prev, 'hash': h, 'alg': 'sha256', 'sig': hmac.new(self.key, h.encode(), hashlib.sha256).hexdigest()})
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, 'a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
                f.flush(); os.fsync(f.fileno())
            self._anchor(row)
            return row

    def _anchor(self, row):
        line = f"{row['seq']}\t{row['hash']}\t{row['sig']}\t{row['ts']}\n"
        try:
            fd = os.open(self.anchor_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, 'a') as f:
                f.write(line)
        except OSError:
            pass
        if self.anchor_url:
            def post():
                try:
                    req = urllib.request.Request(self.anchor_url, data=json.dumps({'seq': row['seq'], 'hash': row['hash'], 'sig': row['sig'], 'ts': row['ts']}).encode(),
                                                 headers={'Content-Type': 'application/json'}, method='POST')
                    urllib.request.urlopen(req, timeout=5).read()
                except Exception:
                    pass
            threading.Thread(target=post, daemon=True).start()

    def verify(self):
        rows = self.rows()
        anchors = {}
        try:
            with open(self.anchor_path) as f:
                for line in f:
                    p = line.rstrip('\n').split('\t')
                    if len(p) >= 3:
                        anchors[int(p[0])] = (p[1], p[2])
        except FileNotFoundError:
            pass
        signed = unsigned = 0
        for i, r in enumerate(rows):
            if i > 0 and r.get('prev') != rows[i - 1].get('hash'):
                return {'ok': False, 'seq': r.get('seq'), 'why': f"entry #{r.get('seq')} does not point at the hash of #{rows[i - 1].get('seq')}"}
            fields = FIELDS_V2 if r.get('v') == 2 else FIELDS_V1
            body = (r.get('prev', '') + canon(r, fields))
            want = hashlib.sha256(body.encode('utf-8')).hexdigest() if r.get('alg') == 'sha256' else _fnv(body)
            if want != r.get('hash'):
                return {'ok': False, 'seq': r.get('seq'), 'why': f"entry #{r.get('seq')} was changed after it was written (its hash no longer matches its contents)"}
            if r.get('sig') is not None:
                good = hmac.new(self.key, str(r['hash']).encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(good, r['sig']):
                    return {'ok': False, 'seq': r.get('seq'), 'why': f"entry #{r.get('seq')} has a signature this server did not make"}
                signed += 1
            else:
                unsigned += 1
            a = anchors.get(r.get('seq'))
            if a and a[0] != r.get('hash'):
                return {'ok': False, 'seq': r.get('seq'), 'why': f"entry #{r.get('seq')} no longer matches the checkpoint kept outside the records folder"}
        missing = [s for s in anchors if s > (rows[-1]['seq'] if rows else 0)]
        if missing:
            return {'ok': False, 'seq': min(missing), 'why': f'entries after #{rows[-1]["seq"] if rows else 0} were removed (checkpoints still exist for them)'}
        return {'ok': True, 'count': len(rows), 'signed': signed, 'unsigned_legacy': unsigned, 'anchored': len(anchors)}
