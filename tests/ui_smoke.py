#!/usr/bin/env python3
"""Browser smoke test of the real page: starts its own server (temporary records and keys, no WildGuard), drives your
installed Chrome with Playwright, and checks the main flows end to end: block, allow, held action + reviewer sign-in,
maker is not checker, admin-only settings, a document edit and the refusal to undo a redaction, a tool call, and the ledger.
Needs:  pip install playwright   (it uses the Chrome you already have; nothing else is downloaded)
Run:    python3 tests/ui_smoke.py [--show]      (--show opens the window so you can watch)"""
import os, shutil, subprocess, sys, tempfile, time, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit('Playwright is not installed. Run: pip install playwright')
PORT = 8895
TMP = tempfile.mkdtemp(prefix='verifai-ui-')
env = dict(os.environ, VERIFAI_PORT=str(PORT), VERIFAI_RECORDS=os.path.join(TMP, 'records'), VERIFAI_HOME=os.path.join(TMP, 'home'), WILDGUARD_URL='',
           VERIFAI_LLM_API_KEY='', GROQ_API_KEY='')
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py')], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
URL = f'http://127.0.0.1:{PORT}/'
bad = 0
def check(name, cond, extra=''):
    global bad; bad += not cond; print(('ok   ' if cond else 'FAIL ') + name + (f'  [{extra}]' if extra and not cond else ''), flush=True)
try:
    for _ in range(120):
        try: urllib.request.urlopen(URL + 'api/status', timeout=2); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel='chrome', headless='--show' not in sys.argv)
        except Exception as e:
            sys.exit(f'Could not start Chrome ({str(e).splitlines()[0]}). Install Google Chrome, or run: playwright install chromium')
        page = browser.new_page(viewport={'width': 1360, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        # the wrong-PIN (401) and maker-is-checker (403) checks below make the browser log those two responses on purpose
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' and 'favicon' not in m.text and not any(f'status of {c}' in m.text for c in (401, 403)) else None)
        failed = []
        def on_response(r):
            if r.status in (400, 404, 405, 413, 500, 503):
                try: body = r.text()[:140]
                except Exception: body = ''
                failed.append(f'{r.status} {r.request.method} {r.url.replace(URL, "/")} {body}')
        page.on('response', on_response)
        page.goto(URL); page.wait_for_selector('#siForm', state='visible'); page.wait_for_timeout(1200)
        def try_login(tab, user, pin):
            page.click(tab); page.fill('#siUser', user); page.fill('#siPin', pin); page.click('#siGo'); page.wait_for_timeout(1100)
            return page.inner_text('#siMsg')
        def who(): return page.evaluate("document.querySelector('#whoBtn').innerText")
        def start_and_sign_out():
            page.locator('#home [data-go="input"]:visible').first.click(); page.wait_for_selector('#whoBtn:visible'); page.click('#whoBtn'); page.wait_for_selector('#siForm', state='visible'); page.wait_for_timeout(700)
        check('the first page is a sign-in page with User and Admin doors', page.is_visible('#siTabUser') and page.is_visible('#siTabAdmin') and page.is_visible('#siGuest'))
        check('a wrong PIN is refused', 'Wrong' in try_login('#siTabUser', 'ravi', '0000'))
        check('an admin account is refused at the User door', 'admin account' in try_login('#siTabUser', 'meena', '3333'))
        check('a user account is refused at the Admin door', 'not an admin' in try_login('#siTabAdmin', 'ravi', '2222'))
        try_login('#siTabAdmin', 'meena', '3333')
        check('an admin signs in at the Admin door', 'Meena' in who() and 'admin' in who())
        start_and_sign_out()
        check('signing out returns to the sign-in page', page.is_visible('#siForm') and 'Sign in' in who())
        try_login('#siTabUser', 'ravi', '2222')
        check('a user signs in at the User door', 'Ravi' in who())
        page.reload(); page.wait_for_timeout(1500)
        check('a page reload keeps the person signed in', page.is_visible('#home') and 'Ravi' in who())
        start_and_sign_out()
        page.click('#siGuest'); page.wait_for_timeout(500)
        page.locator('#home [data-go="input"]:visible').first.click(); page.wait_for_selector('#whoBtn'); page.wait_for_timeout(1000)
        n = 0
        def ask(text, first=False):
            """Types a request, waits for the server's decision to finish drawing, returns the verdict heading."""
            global n
            n += 1
            box, send = ('#prompt1', '#send1') if first else ('#prompt2', '#send2')
            page.fill(box, text); page.click(send)
            page.wait_for_function(f"document.querySelectorAll('#timeline > *').length >= {n} && !document.querySelector('#right .progress') && document.querySelector('#right .result h2')", timeout=60000)
            page.wait_for_timeout(400)
            return page.inner_text('#right .result h2').strip()
        def login(user, pin):
            page.wait_for_selector('#authModal:not(.hide)', timeout=10000)
            page.select_option('#auUser', user); page.fill('#auPin', pin); page.click('#auGo')
            page.wait_for_selector('#authModal.hide', state='attached', timeout=10000); page.wait_for_timeout(500)
        def toast(): return page.inner_text('#toast')

        check('the page loads and offers sign-in', 'Sign in' in page.inner_text('#whoBtn'))
        check('a destructive request is blocked', ask('Drop the production database', first=True) == 'Blocked')
        check('a plain question is allowed', ask('What is 5+2?') == 'Allowed')
        ask('Delete the audit logs for last week')
        check('after two blocks a modest action needs a person', ask('Refund order 8841 for Rs 800') == 'Needs review')
        page.wait_for_selector('#approvalModal:not(.hide)', timeout=10000)
        check('a held request offers Approve, Reject and Ask a reviewer', all(x in page.inner_text('#approvalModal') for x in ('Approve and run', 'Reject', 'Ask a reviewer')))
        page.click('[data-apm="approved"]'); page.wait_for_timeout(1500)
        check('the requester can approve their own held request', 'Approved by you' in page.inner_text('#right .result h2'), page.inner_text('#right .result h2'))
        check('another modest action also needs a person', ask('Refund order 8842 for Rs 800') == 'Needs review')
        page.wait_for_selector('#approvalModal:not(.hide)', timeout=10000)
        page.click('[data-apm="ask"]'); page.wait_for_timeout(1500)
        check('"Ask a reviewer" puts the request in the queue', 'Waiting for a reviewer' in page.inner_text('#right .result h2'), page.inner_text('#right .result h2'))
        page.evaluate("window.VerifAIApp.go('queue')"); page.wait_for_selector('#qSignIn', timeout=10000)
        check('the Reviews tab asks a non-reviewer to sign in', 'Reviewers only' in page.inner_text('#queueBody'))
        page.click('#qSignIn'); login('ravi', '2222'); page.wait_for_selector('.qcard', timeout=10000)
        check('a reviewer sees the sample requests and the one just sent', page.locator('.qtag.sample').count() >= 5 and page.locator('.qcard').count() >= 6, page.locator('.qcard').count())
        page.locator('.qcard', has=page.locator('.qtag.sample')).first.locator('[data-qd="approved"]').click(); page.wait_for_timeout(1500)
        check('a reviewer can approve a queued request', 'Approved' in toast(), toast())
        page.locator('.qcard', has=page.locator('.qtag:not(.sample)')).first.locator('[data-qd="approved"]').click(); page.wait_for_timeout(1500)
        check('a guest cannot get their own request approved by signing in as a reviewer in the same window', 'same window' in toast(), toast())
        page.keyboard.press('Escape'); page.evaluate("document.querySelector('#approvalModal').classList.add('hide')")
        def enter_as_guest():
            page.click('#siGuest'); page.locator('#home [data-go="input"]:visible').first.click(); page.wait_for_selector('#whoBtn:visible'); page.wait_for_timeout(500)
        page.click('#whoBtn'); page.wait_for_selector('#siForm', state='visible'); page.wait_for_timeout(500)
        check('sign out works', page.is_visible('#siForm') and 'Sign in' in who())
        enter_as_guest()
        page.evaluate("window.VerifAIApp.go('config')"); page.wait_for_timeout(500)      # the menu is hidden until there is something to show
        check('settings are read-only for a guest', 'Read-only' in page.inner_text('#cfgNote') and page.is_disabled('#t1'))
        page.wait_for_selector('[data-safety]', timeout=10000)
        check('the Models card lists both safety models (WildGuard and Llama Guard)', page.locator('[data-safety]').count() == 2 and 'Llama Guard' in page.inner_text('#safetyOptions'))
        check('the safety model cannot be switched by a guest', page.is_disabled('[data-safety="llamaguard"]'))
        page.click('#whoBtn'); page.wait_for_selector('#siForm', state='visible'); try_login('#siTabAdmin', 'meena', '3333')
        page.locator('#home [data-go="input"]:visible').first.click(); page.wait_for_selector('#whoBtn:visible'); page.evaluate("window.VerifAIApp.go('config')"); page.wait_for_timeout(600)
        check('settings unlock for an admin', 'admin' in page.inner_text('#cfgNote').lower() and not page.is_disabled('#t1'))
        page.wait_for_selector('[data-safety]:not([disabled])', timeout=10000)
        page.click('[data-safety="llamaguard"]'); page.wait_for_timeout(1800)
        check('an admin can switch the safety model to Llama Guard (with a warning if it is not running)', 'Llama Guard' in toast() and 'in use' in page.inner_text('[data-safety="llamaguard"]'), toast())
        page.click('[data-safety="wildguard"]'); page.wait_for_timeout(1800)
        check('and back to WildGuard', 'in use' in page.inner_text('[data-safety="wildguard"]'))
        page.click('#whoBtn'); page.wait_for_selector('#siForm', state='visible'); enter_as_guest()
        n = 0        # signing out started a fresh session: autonomy earned so far in this run is forgotten
        page.wait_for_selector('#sampleDoc'); page.click('#sampleDoc'); page.fill('#prompt1', 'Redact all email addresses')
        n += 1; page.click('#send1')
        page.wait_for_function(f"document.querySelectorAll('#timeline > *').length >= {n} && !document.querySelector('#right .progress') && document.querySelector('#right .result h2')", timeout=60000)
        page.wait_for_timeout(500)
        check('a document edit is applied and shown', 'EMAIL REDACTED' in page.evaluate("[...document.querySelectorAll('.docline')].map(x => x.textContent).join(' ')"), page.inner_text('#right')[:160])
        check('going back past a redaction is refused', ask('undo') == 'Blocked')

        page.click('#nav [data-v="ledger"]'); page.click('#verifyLedger'); page.wait_for_timeout(1500)
        check('the ledger verifies', 'Ledger intact' in page.inner_text('#verifyOut'), page.inner_text('#verifyOut')[:100])
        check('no script errors appeared in the page', not errors, errors[:2])
        check('no request failed with a client or server error', not failed, failed[:4])
        browser.close()
finally:
    srv.terminate(); shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if bad else 0)
