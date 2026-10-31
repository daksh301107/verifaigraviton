"""People and sessions. Users live in policy_data/users.csv (id, name, role, salt, pin_hash; PBKDF2).
Roles: requester (default for anyone not signed in), reviewer (can decide held actions, but never their own request),
admin (can also change policy limits and risk settings). The sample users in the repo have sample PINs: replace them."""
import csv, hashlib, hmac, os, secrets, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))
USERS = os.path.join(ROOT, 'policy_data', 'users.csv')
USERS_SAMPLE = os.path.join(ROOT, 'policy_data', 'users.sample.csv')      # demo accounts, used only when users.csv does not exist
ROLES = {'requester': 0, 'reviewer': 1, 'admin': 2}
_lock = threading.Lock()
_fails = {}          # user id -> [count, locked_until]


def hash_pin(pin, salt):
    return hashlib.pbkdf2_hmac('sha256', str(pin).encode(), bytes.fromhex(salt), 120_000).hex()


def users():
    for path in (USERS, USERS_SAMPLE):
        try:
            with open(path, encoding='utf-8', newline='') as f:
                return {r['id'].strip().lower(): r for r in csv.DictReader(f) if r.get('id')}
        except FileNotFoundError:
            continue
    return {}


def login(uid, pin):
    """Returns (user dict, error)."""
    uid = (uid or '').strip().lower()
    with _lock:
        st = _fails.get(uid, [0, 0])
        if st[1] > time.time():
            return None, f'Too many wrong PINs. Try again in {int(st[1] - time.time())} seconds.'
        u = users().get(uid)
        ok = bool(u) and hmac.compare_digest(hash_pin(pin, u['salt']), u['pin_hash'])
        if not ok:
            st[0] += 1
            if st[0] >= 5:
                st = [0, time.time() + 60]
            _fails[uid] = st
            return None, 'Wrong user or PIN.'
        _fails.pop(uid, None)
        return {'id': u['id'].strip().lower(), 'name': u['name'], 'role': u['role'].strip().lower()}, None


def at_least(user, role):
    return bool(user) and ROLES.get(user.get('role'), -1) >= ROLES[role]


class Sessions:
    """A session = one browser tab's working state: who is signed in, and the action history that feeds
    earned autonomy and trajectory risk. Held on the server so the browser cannot change it."""
    def __init__(self, ttl=12 * 3600, cap=300):
        self.ttl, self.cap = ttl, cap
        self.lock = threading.Lock()
        self.d = {}

    def new(self, user=None):
        with self.lock:
            now = time.time()
            for k in [k for k, v in self.d.items() if now - v['seen'] > self.ttl]:
                del self.d[k]
            while len(self.d) >= self.cap:
                del self.d[min(self.d, key=lambda k: self.d[k]['seen'])]
            sid = secrets.token_urlsafe(24)
            self.d[sid] = {'user': user, 'hist': [], 'seen': now, 'created': now}
            return sid

    def get(self, sid):
        with self.lock:
            s = self.d.get(sid or '')
            if s:
                s['seen'] = time.time()
            return s

    def reset(self, sid):
        with self.lock:
            s = self.d.get(sid or '')
            if s:
                s['hist'] = []
            return s
