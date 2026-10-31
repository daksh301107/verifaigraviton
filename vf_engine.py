"""Starts engine_worker.js (Node) and talks to it over stdin/stdout, one JSON line per request.
The decision engine therefore runs inside the server, not in the user's browser."""
import json, os, shutil, subprocess, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))


def find_node():
    n = shutil.which('node')
    if n:
        return n
    for c in ('/opt/homebrew/bin/node', '/usr/local/bin/node', os.path.expanduser('~/.nvm/versions/node/current/bin/node')):
        if os.path.exists(c):
            return c
    return None


class Engine:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.error = None
        self.n = 0
        self.schema = None

    def _start(self):
        node = find_node()
        if not node:
            self.error = 'Node.js was not found. The decision engine runs in Node (install it from nodejs.org, then restart).'
            return False
        self.proc = subprocess.Popen([node, os.path.join(ROOT, 'engine_worker.js')], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.error = None
        if self.schema is not None:
            self._raw('setSchema', self.schema, 20)
        return True

    def _raw(self, op, args, timeout):
        self.n += 1
        rid = self.n
        self.proc.stdin.write(json.dumps({'id': rid, 'op': op, 'args': args}) + '\n')
        self.proc.stdin.flush()
        # one request at a time (the lock), so the next line out is the answer; a watchdog thread enforces the timeout
        done = threading.Event()
        box = {}

        def read():
            try:
                box['line'] = self.proc.stdout.readline()
            finally:
                done.set()
        t = threading.Thread(target=read, daemon=True)
        t.start()
        if not done.wait(timeout):
            self.proc.kill()
            raise TimeoutError(f'decision engine did not answer within {timeout}s')
        line = box.get('line', '')
        if not line:
            raise RuntimeError('decision engine stopped unexpectedly')
        j = json.loads(line)
        if not j.get('ok'):
            raise RuntimeError(j.get('error', 'engine error'))
        return j['result']

    def call(self, op, args=None, timeout=30):
        """Returns the engine's answer, restarting the worker once if it died. Raises if the engine is unavailable."""
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.proc is None or self.proc.poll() is not None:
                        if not self._start():
                            raise RuntimeError(self.error)
                    return self._raw(op, args or {}, timeout)
                except (TimeoutError, RuntimeError, BrokenPipeError, OSError, ValueError) as e:
                    self.error = str(e)
                    try:
                        if self.proc:
                            self.proc.kill()
                    except Exception:
                        pass
                    self.proc = None
                    if attempt == 1:
                        raise
            raise RuntimeError(self.error)

    def set_schema(self, payload):
        self.schema = payload
        try:
            self.call('setSchema', payload, 20)
        except Exception:
            pass

    def status(self):
        try:
            self.call('ping', {}, 10)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
