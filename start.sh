#!/bin/sh
# One command for the whole demo:   ./start.sh            start whatever is not running, then check everything
#                                   ./start.sh check      only check (a "ready to demo?" report)
#                                   ./start.sh stop       stop what this script started
#                                   ./start.sh --llamaguard also start Llama Guard (the second safety model, port 8002)
#                                   ./start.sh --llama      also start the local Llama agent (port 8001); by default the agent is Groq
# Services that are already running (for example in your own terminal tabs) are left alone and reused.
cd "$(dirname "$0")" || exit 1
ROOT=$(pwd); LOGS="$ROOT/logs"; PIDS="$LOGS/pids"; mkdir -p "$LOGS"
PORT="${VERIFAI_PORT:-8765}"; WG_PORT="${WILDGUARD_PORT:-8000}"; LL_PORT="${LLAMA_PORT:-8001}"; LG_PORT=8002
MODE="start"; USE_LLAMA=0; USE_LG=0
for a in "$@"; do case "$a" in check) MODE=check ;; stop) MODE=stop ;; --llama) USE_LLAMA=1 ;; --llamaguard) USE_LG=1 ;; --no-llama) USE_LLAMA=0 ;; -h|--help) sed -n 2,6p "$0"; exit 0 ;; esac; done
# commands can be overridden for testing
WG_CMD="${VF_WILDGUARD_CMD:-python3 wildguard_local.py}"; LL_CMD="${VF_LLAMA_CMD:-python3 llama_local.py}"; LG_CMD="${VF_LLAMAGUARD_CMD:-env LLAMA_MODEL=${LLAMAGUARD_REPO:-mlx-community/Llama-Guard-3-1B-chat-mlx-4bit} LLAMA_PORT=8002 python3 llama_local.py}"; SV_CMD="${VF_SERVER_CMD:-python3 server.py}"

up()   { curl -s -m 3 -o /dev/null "http://127.0.0.1:$1$2"; }
green() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
red()   { printf '  \033[31m✗\033[0m %s\n' "$1"; }
amber() { printf '  \033[33m!\033[0m %s\n' "$1"; }

if [ "$MODE" = stop ]; then
  [ -f "$PIDS" ] || { echo "Nothing started by this script."; exit 0; }
  while read -r name pid; do pkill -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null && echo "stopped $name ($pid)"; done < "$PIDS"; rm -f "$PIDS"; exit 0
fi

echo "VerifAI preflight"
# ---- prerequisites
command -v node >/dev/null 2>&1 && green "Node.js $(node --version) (the decision engine runs in it)" || { red "Node.js is missing. Install it from https://nodejs.org, then run this again."; exit 1; }
python3 -c "import sklearn, joblib, sqlalchemy" 2>/dev/null && green "Python packages (scikit-learn, joblib, sqlalchemy)" || { red "Missing Python packages. Run: pip install scikit-learn joblib sqlalchemy"; exit 1; }
[ -f .env ] && green ".env found" || amber ".env not found (the hosted LLM key is optional; Llama or WildGuard URL may be set there)"

start_bg() {   # name port health cmd
  if up "$2" "$3"; then green "$1 is already running on port $2"; return 0; fi
  [ "$MODE" = check ] && { red "$1 is not running (port $2)"; return 1; }
  echo "  starting $1 (log: logs/$1.log) ..."
  nohup sh -c "$4" > "$LOGS/$1.log" 2>&1 &
  echo "$1 $!" >> "$PIDS"
  return 0
}
wait_up() {    # name port health seconds
  i=0; while [ $i -lt "$4" ]; do up "$2" "$3" && { green "$1 is ready"; return 0; }; i=$((i+2)); sleep 2; done
  red "$1 did not come up in $4 seconds. See logs/$1.log"; return 1
}

OK=1
# WildGuard
start_bg wildguard "$WG_PORT" / "$WG_CMD" || OK=0
# Llama
if [ "$USE_LLAMA" = 1 ]; then start_bg llama "$LL_PORT" / "$LL_CMD" || OK=0; else amber "Local Llama not started: the agent uses the hosted model (Groq) unless .env points at local Llama"; fi
# Llama Guard (optional second safety model)
if [ "$USE_LG" = 1 ]; then start_bg llamaguard "$LG_PORT" / "$LG_CMD" || OK=0; elif up "$LG_PORT" /; then green "llamaguard is running on port $LG_PORT"; else amber "Llama Guard is not running (optional; start it with ./start.sh --llamaguard to be able to switch to it)"; fi
# server
start_bg verifai "$PORT" /api/status "$SV_CMD" || OK=0
if [ "$MODE" != check ]; then
  up "$WG_PORT" / || wait_up wildguard "$WG_PORT" / 300 || OK=0
  [ "$USE_LLAMA" = 1 ] && { up "$LL_PORT" / || wait_up llama "$LL_PORT" / 600 || OK=0; }
  [ "$USE_LG" = 1 ] && { up "$LG_PORT" / || wait_up llamaguard "$LG_PORT" / 900 || OK=0; }
  up "$PORT" /api/status || wait_up verifai "$PORT" /api/status 90 || OK=0
fi

# ---- end-to-end check through the real API
if up "$PORT" /api/status; then
  python3 - "$PORT" <<'PY' || OK=0
import json, sys, urllib.request
B = f'http://127.0.0.1:{sys.argv[1]}'
def call(path, data=None, sid=None):
    req = urllib.request.Request(B + path, data=None if data is None else json.dumps(data).encode(), method='GET' if data is None else 'POST')
    if sid: req.add_header('X-VerifAI-Session', sid)
    with urllib.request.urlopen(req, timeout=120) as r: return json.load(r)
g = lambda s: print(f'  \033[32m✓\033[0m {s}'); r = lambda s: print(f'  \033[31m✗\033[0m {s}'); a = lambda s: print(f'  \033[33m!\033[0m {s}')
bad = 0
st = call('/api/status'); g('server answers') if st.get('ok') else r('server status not ok')
if st.get('engine'): g('decision engine is running')
else: r('decision engine is not running (is Node.js working?)'); bad = 1
for name, path in (('policy model', '/api/policy-check/status'), ('impact model', '/api/intent-impact/status'), ('LLM agent', '/api/agent/status')):
    try:
        j = call(path)
        if j.get('ok'): g(f"{name} is connected" + (f" ({j['model']}, {'local' if j.get('local') else 'hosted by Groq'})" if name == 'LLM agent' else ''))
        else: a(f"{name} is not available: {j.get('error') or 'not loaded'}")
    except Exception as e: a(f'{name}: {e}')
sid = call('/api/session/new', {})['sid']
try:
    m = call('/api/models', None, sid)
    for o in m['safety']['options']:
        use = o['id'] == m['safety']['active']
        if use and o['running']: g(f"safety check in use: {o['name']} is running")
        elif use: r(f"safety check in use: {o['name']} is NOT running (requests will be held for a person)"); bad = 1
        elif o['running']: g(f"{o['name']} is running too (available in Settings)")
        else: a(f"{o['name']} is not running (optional)")
except Exception as e: a(f'could not read the models: {e}')
t = call('/api/evaluate', {'text': 'Drop the production database', 'noSession': True}, sid)
g('a harmful request is blocked') if t['res']['verdict'] == 'BLOCK' else (r(f"expected BLOCK, got {t['res']['verdict']}"), globals().update(bad=1))
t = call('/api/evaluate', {'text': 'What is 5+2?', 'noSession': True}, sid)
if t['res']['verdict'] == 'ALLOW': g('a plain question is allowed')
else: r(f"expected ALLOW, got {t['res']['verdict']} ({t['res']['trigger'][:60]})"); bad = 1
if t.get('degraded'): a('a safety check did not run for that request: ' + ', '.join(t['degraded']) + ' (actions will be held for a person)')
v = call('/api/ledger/verify')
g(f"ledger verifies ({v.get('count')} entries)") if v.get('ok') else (r('ledger problem: ' + str(v.get('why'))), globals().update(bad=1))
sys.exit(bad)
PY
else
  OK=0
fi
echo
if [ "$OK" = 1 ]; then printf '\033[32mReady.\033[0m Open http://localhost:%s\n' "$PORT"; else printf '\033[31mNot ready.\033[0m Fix the lines marked ✗ above, then run ./start.sh again.\n'; exit 1; fi
