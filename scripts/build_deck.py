#!/usr/bin/env python3
"""Fills the Hack-AI-Thon solution template with VerifAI's content, keeping the template's design, logos and footer.
   python3 scripts/build_deck.py [path/to/template.pptx]   ->  VerifAI_Solution_Presentation.pptx
Text that this script does not know (college name, other team members) is left as the template's grey [placeholders]."""
import copy, math, os, sys
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/Downloads/Hack-AI-Thon_Solution_Presentation_Template.pptx')
OUT = os.path.join(HERE, 'VerifAI_Solution_Presentation.pptx')
INK = '1F2937'
prs = Presentation(TEMPLATE)
S = list(prs.slides)
warn = []

# ---------------------------------------------------------------- helpers
def walk(shapes):
    for sh in shapes:
        yield sh
        if sh.shape_type == 6:
            yield from walk(sh.shapes)

def find(slide, startswith):
    for sh in walk(slide.shapes):
        if sh.has_text_frame and sh.text_frame.text.strip().startswith(startswith):
            return sh
    raise KeyError(startswith)

def named(slide, name):
    for sh in walk(slide.shapes):
        if sh.name == name:
            return sh
    raise KeyError(name)

def inch(sh, x=None, y=None, w=None, h=None):
    if x is not None: sh.left = Inches(x)
    if y is not None: sh.top = Inches(y)
    if w is not None: sh.width = Inches(w)
    if h is not None: sh.height = Inches(h)

def fit(paras, w_in, h_in, max_pt, min_pt, bullet):
    """Largest font size (in 0.5 pt steps) at which the paragraphs should fit in the box."""
    pt = max_pt
    while pt >= min_pt:
        cpl = max(8, (w_in - 0.05 - (0.2 if bullet else 0)) * 72 / (pt * 0.45))
        lines = sum(max(1, math.ceil(len(t) / cpl)) for t in paras)
        if lines * pt * 1.2 / 72 + (len(paras) - 1) * 3 / 72 <= h_in:
            return pt
        pt -= 0.5
    return None

def fill(sh, paras, max_pt=12, min_pt=8, bullet=False, color=INK, align=None, anchor=None, italic=False, bold=None):
    if isinstance(paras, str): paras = [paras]
    w, h = sh.width / 914400, sh.height / 914400
    pt = fit(paras, w, h, max_pt, min_pt, bullet)
    if pt is None:
        pt = min_pt; warn.append(f'{sh.name}: text may overflow at {min_pt}pt ({sum(len(p) for p in paras)} chars in {w:.1f}x{h:.1f}in)')
    body = sh.text_frame._txBody
    ps = body.findall(qn('a:p')); base = copy.deepcopy(ps[0])
    for p in ps: body.remove(p)
    bp = body.find(qn('a:bodyPr'))
    if anchor: bp.set('anchor', anchor)
    for af in bp.findall(qn('a:normAutofit')): bp.remove(af)
    for t in paras:
        p = copy.deepcopy(base)
        runs = p.findall(qn('a:r'))
        for r in runs[1:]: p.remove(r)
        r = runs[0]; r.find(qn('a:t')).text = t
        rPr = r.find(qn('a:rPr'))
        rPr.set('sz', str(int(pt * 100)))
        if italic: rPr.set('i', '1')
        else: rPr.attrib.pop('i', None)
        if bold is not None: rPr.set('b', '1' if bold else '0')
        sf = rPr.find(qn('a:solidFill'))
        if sf is None:
            sf = etree.SubElement(rPr, qn('a:solidFill')); rPr.insert(0, sf)
        for c in list(sf): sf.remove(c)
        etree.SubElement(sf, qn('a:srgbClr')).set('val', color)
        e = p.find(qn('a:endParaRPr'))
        if e is not None: e.set('sz', str(int(pt * 100)))
        pPr = p.find(qn('a:pPr'))
        if pPr is None: pPr = etree.SubElement(p, qn('a:pPr')); p.insert(0, pPr)
        if align: pPr.set('algn', align)
        if bullet:
            pPr.set('marL', '150000'); pPr.set('indent', '-150000')
            for c in list(pPr): pPr.remove(c)
            etree.SubElement(pPr, qn('a:spcAft')).append(etree.Element(qn('a:spcPts'), val='250'))
            etree.SubElement(pPr, qn('a:buSzPct')).set('val', '100000')
            etree.SubElement(pPr, qn('a:buChar')).set('char', '•')
        body.append(p)

def runs(sh, parts, size=None):
    """parts: [(text, filled?)]. Filled text is dark and upright; the rest keeps the template's grey italic placeholder look."""
    body = sh.text_frame._txBody
    for extra in body.findall(qn('a:p'))[1:]: body.remove(extra)
    p = body.findall(qn('a:p'))[0]
    r0 = p.findall(qn('a:r'))[0]
    for r in p.findall(qn('a:r')): p.remove(r)
    end = p.find(qn('a:endParaRPr'))
    for text, filled in parts:
        r = copy.deepcopy(r0); r.find(qn('a:t')).text = text
        rPr = r.find(qn('a:rPr'))
        if size: rPr.set('sz', str(int(size * 100)))
        if filled:
            rPr.attrib.pop('i', None)
            for c in list(rPr.find(qn('a:solidFill'))): rPr.find(qn('a:solidFill')).remove(c)
            etree.SubElement(rPr.find(qn('a:solidFill')), qn('a:srgbClr')).set('val', INK)
        if end is not None: end.addprevious(r)
        else: p.append(r)

def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text

def say(slide, bullets, max_pt=11):
    """The template's WHAT TO SAY box: replaced with our own talk track."""
    sh = [s for s in walk(slide.shapes) if s.has_text_frame and s.text_frame.text.startswith(('Describe the end', 'Walk left', 'State exactly', 'Separate what'))][0]
    fill(sh, bullets, max_pt=max_pt, min_pt=9, bullet=True)

# ---------------------------------------------------------------- slide 1: title
s = S[0]; tb = s.shapes[0]
body = tb.text_frame._txBody; ps = body.findall(qn('a:p'))
extra = copy.deepcopy(ps[2]); extra.findall(qn('a:r'))[0].find(qn('a:t')).text = 'VerifAI: Agentic AI Guardrail Validator  |  Team Delta'
extra.findall(qn('a:r'))[0].find(qn('a:rPr')).set('sz', '2000')
blank = copy.deepcopy(ps[1])
body.append(blank); body.append(extra)
notes(s, 'Open with the problem in one line: AI agents can now act, so someone has to check each action before it runs. VerifAI is that check.')

# ---------------------------------------------------------------- slide 2: team
s = S[1]
fill(find(s, '[TEAM NAME]'), 'DELTA', max_pt=32, min_pt=28, bold=True, color='003B7A')
runs(find(s, '[Name 1]'), [('Daksh Aggarwal', True), ('   •   [Name 2]   •   [Name 3]', False)])
fill(find(s, '[Problem statement ID'), 'ET-03  |  Agentic AI Guardrail Validator', max_pt=13, min_pt=10)
fill(find(s, '[UNO Q'), 'Other: safety models on-device (Apple MLX); agent hosted (Groq)', max_pt=11, min_pt=9, italic=False)
notes(s, 'Introduce the team and the problem we chose: ET-03, check that an agent\'s planned action is safe, authorized, explainable and reversible. We built and ran everything on a laptop, no cloud needed.')

# ---------------------------------------------------------------- slide 3: problem statement
s = S[2]
def put(prefix, text, **kw):
    sh = find(s, prefix); inch(sh, h=kw.pop('h', None), y=kw.pop('y', None)); fill(sh, text, anchor='t', **kw)
put('[Paste the selected', 'ET-03, Agentic AI Guardrail Validator: check whether an AI agent\'s planned action is safe, authorized, explainable and reversible. Expected demo: agent action input, approve or block decision, guardrail explanation.', h=1.2, max_pt=17, min_pt=12)
put('[Primary user', 'Teams that let AI agents act on real systems (payments, HR, support, IT operations), and the customers whose money and data those systems hold.', h=0.85, max_pt=12, min_pt=9)
put('[Business, social', 'One wrong agent action (a bad refund, deleted backups, data sent out) costs money, trust and legal standing, and often cannot be undone.', h=0.85, max_pt=12, min_pt=9)
put('[What is difficult', 'Agents act on text. Keyword filters miss reworded or disguised requests, ignore whether an action can be undone, and leave no reason or trustworthy record.', h=2.3, max_pt=14, min_pt=10)
put('[One real example', 'Example: “Delete all customer backups tonight.” A simple filter allows it, yet nothing could be restored. Five ₹200 transfers each look fine alone but add up past the limit.', h=2.3, max_pt=14, min_pt=10)
put('[What outcome proves', 'Every action gets ALLOW, REVIEW or BLOCK with a reason in plain words. Harmful or irreversible actions never run without a person, and every decision sits on a tamper-evident record.', h=1.9, max_pt=12, min_pt=9)
notes(s, 'Read the problem statement, then the gap: agents act on text, filters are brittle, and nobody records why. Use the backup example, it is easy to feel. Success is a decision, a reason and a record for every action.')

# ---------------------------------------------------------------- slide 4: proposed solution
s = S[3]
sh = find(s, '[Our solution uses'); inch(sh, y=1.58, h=0.46)
fill(sh, 'Our solution uses deterministic rules, on-device safety and risk models, and human approval to help teams let AI agents act while every action stays safe, authorized, explainable and reversible.', max_pt=14, min_pt=11, anchor='t')
for pref, text in (('[What data, event', 'A person types, speaks or uploads a document, or an agent sends a structured tool call (amount, table, count). Nothing runs yet.'),
                   ('[What does the system', 'The server reads the request, even if disguised, applies 27 hard rules, WildGuard and a policy model, scores risk and reversibility, checks the autonomy the agent has earned, then decides.'),
                   ('[What result, action', 'ALLOW, REVIEW or BLOCK with the reason in plain words and what-if advice. An edited document or a sandbox tool run, and a signed ledger entry.')):
    sh = find(s, pref); inch(sh, y=3.08, h=1.35); fill(sh, text, max_pt=12, min_pt=9, anchor='t')
kv = s.shapes.add_textbox(Inches(0.78), Inches(5.1), Inches(11.7), Inches(0.33)); kv.text_frame.word_wrap = True
r = kv.text_frame.paragraphs[0].add_run()
r.text = 'Reversibility is scored, not guessed. The server decides, so a page cannot skip it. If a check cannot run, a person decides, never “allow”. Nobody approves their own request.'
r.font.size = Pt(10); r.font.color.rgb = RGBColor.from_string(INK)
for m in ('margin_left', 'margin_right', 'margin_top', 'margin_bottom'): setattr(kv.text_frame, m, 0)
say(s, ['Journey: a support agent asks the AI to refund an order. The server checks it and holds it, a reviewer signs in and approves, and the record shows who and why.',
        'Today the prototype already runs this end to end on a laptop: typed or spoken requests, documents, tool calls and a signed ledger.'])
notes(s, 'Walk the three boxes with one story: refund request in, checks and a hold in the middle, a named approval and a signed record out. Then the differentiator: reversibility is scored, the server decides, fail closed, accountable approvals.')

# ---------------------------------------------------------------- slide 5: architecture
s = S[4]
arch = {
    '[User, sensor': ['Browser page: HTML, CSS, plain JavaScript', 'Typed or spoken (Web Speech API)', 'Files: txt, md, csv, json, docx, pdf, png, jpg', 'Scans read offline (Tesseract.js)', 'Sign-in for reviewers and admins'],
    '[UI, APIs': ['Python 3.12 server, localhost only', 'Decision engine in a Node.js worker', '27 hard policy rules + text normaliser', 'Risk score, then min(AAL, ACL)', 'Reviewer is never the requester', 'Tool gateway to a sandbox database'],
    '[Model, prompt': ['Safety: WildGuard 7B or Llama Guard, on-device (MLX)', 'Agent: Groq-hosted gpt-oss-20b', 'scikit-learn impact and policy models', 'SQLAlchemy + SQLite reversal graph', 'Sanctions and consent lists (CSV)'],
    '[Dashboard, alert': ['ALLOW / REVIEW / BLOCK with the reason', 'Edited document or sandbox tool run', 'Dashboard (Chart.js), what-if, report', 'Signed, hash-chained ledger', 'Answers read aloud'],
}
for pref, items in arch.items():
    sh = find(s, pref); inch(sh, y=2.2, h=1.14); fill(sh, items, max_pt=9.5, min_pt=7.5, bullet=True, align='l', anchor='t')
low = {
    '[Languages, frameworks': ['Python and JavaScript (browser and Node.js)', 'Models: WildGuard 7B / Llama Guard, Groq gpt-oss-20b, TF-IDF + logistic regression', 'Libraries: scikit-learn, SQLAlchemy, Apple MLX, Chart.js, pdf.js', 'Cloud: only the agent (Groq); safety runs on one 16 GB MacBook'],
    '[Why these components': ['Safety on-device: the check never leaves the Mac; only the agent is hosted', 'Rules decide, models advise: same input, same verdict', 'Engine on the server: the page cannot change a verdict', 'Trade-off: the 🤖 agent sends request text to Groq'],
    '[How you tested': ['305 automated checks (211 engine, 66 server, 28 browser)', 'Disguised attacks caught: 9% to 100%', 'Policy model flagged 62 of 63 unseen rewordings', 'A check that cannot run: a person decides'],
}
for pref, items in low.items():
    sh = find(s, pref); inch(sh, y=4.17, h=0.77); fill(sh, items, max_pt=9, min_pt=7.5, bullet=True, align='l', anchor='t')
say(s, ['Left to right: a person or agent asks, the server decides with rules, two safety models you can switch advise, the result is a decision, a reason and a signed record.',
        'Key decision: rules decide, models only advise; if a check cannot run, a person decides. Evidence: 305 checks; disguised attacks caught 9% to 100%.'], max_pt=10)
notes(s, 'Walk the diagram left to right. The most important design decision: the decision is made by deterministic rules on the server and the models only advise; failing closed means a missing check holds the action for a person. Engineering depth: the disguise tests (leetspeak 9% to 100%), ledger tamper tests, maker-checker approvals.')

# ---------------------------------------------------------------- slide 6: AI usage
s = S[5]
def put6(prefix, text, y, h, max_pt, min_pt=8.5):
    sh = find(s, prefix); inch(sh, y=y, h=h); fill(sh, text, max_pt=max_pt, min_pt=min_pt, anchor='t')
put6('[Classification / computer', 'Agentic workflow: an LLM agent (Groq gpt-oss-20b) acts only through the guardrail. LLM safety classification (WildGuard 7B or Llama Guard) and two text classifiers (impact, rule match) judge every action.', 1.56, 0.5, 11)
put6('[What is new, creative', 'AI that guards AI: scores reversibility, earns trust per session, decides on the server, fails closed. Models advise; rules and people decide.', 1.56, 0.52, 10)
put6('[What decision, prediction', 'PREDICT how damaging a mistake would be, and which prohibited action a request resembles however it is worded. CLASSIFY whether it is harmful (WildGuard or Llama Guard, switchable). GENERATE the agent\'s answer, tool choice and refusal explanation (hosted LLM). Never the final call.', 2.46, 1.0, 10.5, 8.5)
put6('[Data/source, model', 'Input: typed or spoken text, documents, tool-call arguments. Safety: WildGuard 7B or Llama Guard (4-bit, on-device, MLX). Agent: Groq gpt-oss-20b (hosted). Two TF-IDF + logistic regression classifiers trained on about 8,000 examples each. The safety check stays on the Mac.', 2.46, 1.0, 10.5, 8.5)
put6('[Accuracy, grounding', 'Why AI, not just rules: our 27 patterns caught 3 of 63 reworded violations, with the policy model 62. On 120 fresh prohibited requests (LLM-written, same family as our training data), none was allowed. The hosted agent chose the right tool 86% of the time (n=168; local Llama 94-95% on earlier sets). Cost: about 40% of ordinary requests are held for a person.', 3.88, 1.0, 10.5, 8.5)
put6('[Time saved, safety', 'Stops costly or irreversible agent mistakes before they run, explains each decision in plain words, and gives auditors a record that resists tampering.', 3.92, 0.9, 10.5)
say(s, ['Why rules alone are not enough: on reworded requests our 27 patterns caught 3 of 63, the policy model flagged 62. Harm in context needs a safety model. Rules still make the final call.',
        'When AI is unsure: a middling match goes to a person, and a check that cannot run holds the action. Numbers: 62 of 63, 93-97% vs 49-58%, about 5 points.'], max_pt=10)
notes(s, 'Be exact about AI use: models with separate jobs (a safety model, two classifiers, and a hosted agent), and none of them makes the final decision. The proof it is not decoration: on 63 hand-written reworded violations the 27 pattern rules alone caught 3, with the policy model 62 were flagged (59 only because of the model). The impact model was right 93-97% on two hand-written sets, against 49-58% for the rules-only estimate. The agent is Groq-hosted, so text given to the agent button leaves the machine; the safety check does not. Tool choice by the hosted agent was 86% on 168 fresh requests (local Llama was 94-95% on earlier sets). Honest limits: the test prompts were written by the team, not outsiders; WildGuard\'s own contribution was not measured separately; a middling match goes to a person rather than being blocked.')

# ---------------------------------------------------------------- slide 7: future, scalability, security
s = S[6]
items7 = {
    '[Feature or user improvement]': 'Put real tools (payments, email, database) behind the gateway, not only the sandbox',
    '[Better model/data': 'Outside testers, a larger labelled set, and real sanctions and consent lists',
    '[Usability / accessibility': 'Reviewer mobile app for approvals; more languages (translate first)',
    '[More users, devices': 'Shared database for sessions and ledger; several engine workers in parallel',
    '[Performance / cloud': 'More model choices per role (Llama Guard added; Qualcomm Cloud AI Playground next)',
    '[Monitoring, maintainability': 'Track decision latency, override rate and model drift',
    '[Authentication / access': 'PIN sign-in, reviewer is not the requester, admin-only settings; company SSO next',
    '[Privacy, encryption': 'Personal data masked before saving; safety models on-device; owner-only files',
    '[Safety, bias, explainability': 'Rules decide, models advise; fail closed; every verdict explained; people review the middle',
}
for pref, text in items7.items():
    sh = find(s, pref); inch(sh, y=sh.top / 914400 - 0.1, h=0.7); fill(sh, text, max_pt=10, min_pt=8, anchor='ctr')
say(s, ['Works now: the pipeline, signed ledger, sign-in and approvals, sandbox tools. Planned: real tools, outside testing, shared storage.',
        'Most realistic next step: wrap one real tool behind the gateway. Most important safeguard: the maker-checker rule with a signed record.'], max_pt=10.5)
notes(s, 'Separate what works today from what is planned. Honest limits: tools are a sandbox, lookup lists are sample data, sign-in is a PIN, and no outsider has tested it yet. The roadmap fixes those in that order.')

# ---------------------------------------------------------------- slide 8: demo
s = S[7]; tb = s.shapes[0]
body = tb.text_frame._txBody; ps = body.findall(qn('a:p'))
sub = copy.deepcopy(ps[-1]); rr = sub.findall(qn('a:r'))
if rr:
    rr[0].find(qn('a:t')).text = 'VerifAI live'; rpr = rr[0].find(qn('a:rPr'))
    if rpr is not None: rpr.set('sz', '2000')
    body.append(sub)
notes(s, 'Demo order (about 4 minutes): 0) The first page is the sign-in page: show the User and Admin doors (an admin account is refused at the User door). Sign in as asha (user, PIN 1111). Do NOT start as ravi: a reviewer cannot approve their own request. 1) "Drop the production database": BLOCK with the rule and the reason. 2) The same request disguised ("D r 0 p t h e ..."): still caught. 3) "Refund order 8841 for Rs 800" after two blocks: REVIEW; click approve, a sign-in window opens: sign in as ravi (reviewer, PIN 2222); show that the requester cannot approve their own. 4) Upload a customer file, redact and change a figure, then "change it back". 5) Tools tab: an allowed read runs in the sandbox, a risky call does not. 6) Ledger tab: Verify. Then sign out and use the Admin door as meena (PIN 3333) to show that Settings are editable only for an admin; mention the tamper test. Fail-closed line: if WildGuard is off, an edit is held instead of allowed.')

# ---------------------------------------------------------------- extra slide after architecture: technology pipeline
src = S[4]
new = prs.slides.add_slide(src.slide_layout)
for nm in ('Text 0', 'Text 1', 'Text 3', 'Slide Number Placeholder 0'):
    new.shapes._spTree.append(copy.deepcopy(named(src, nm)._element))
def settext(sh, t):
    sh.text_frame.paragraphs[0].runs[0].text = t
    for r in sh.text_frame.paragraphs[0].runs[1:]: r.text = ''
settext(named(new, 'Text 0'), 'Technology Pipeline, Stage by Stage')
settext(named(new, 'Text 1'), 'What each guardrail stage is built with, and what happens when it fails.')
settext(named(new, 'Text 3'), 'SHARED WITH ARCHITECTURE')
PAL = {'blue': ('007CC3', 'EAF7FD'), 'purple': ('5417A8', 'F3EEFC'), 'orange': ('F28C28', 'FFF4E8'), 'green': ('63A845', 'EFF7E9')}
def shp(kind, x, y, w, h, line, bg, lw=1.4):
    sp = new.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if kind == MSO_SHAPE.ROUNDED_RECTANGLE: sp.adjustments[0] = 0.05
    sp.fill.solid(); sp.fill.fore_color.rgb = RGBColor.from_string(bg); sp.line.color.rgb = RGBColor.from_string(line); sp.line.width = Pt(lw); sp.shadow.inherit = False
    return sp
def txt(x, y, w, h, paras, pt, color=INK, bold=False, bullet=False, after=3):
    t = new.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf = t.text_frame; tf.word_wrap = True
    for m in ('margin_left', 'margin_right', 'margin_top', 'margin_bottom'): setattr(tf, m, 0)
    for i, p in enumerate(paras):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph(); para.space_after = Pt(after)
        r = para.add_run(); r.text = ('•  ' + p) if bullet else p; r.font.size = Pt(pt); r.font.bold = bold; r.font.color.rgb = RGBColor.from_string(color)
    return t
stages = [
    ('1  Input', 'blue', ['Browser page: HTML, CSS, JS', 'Typed or spoken (Web Speech)', 'pdf.js, JSZip, Tesseract OCR', 'Session id from the server'], 'Server unreachable: nothing runs, and the page says so'),
    ('2  Analyze', 'purple', ['Node.js worker runs engine.js', 'Rules + typo repair', 'Normaliser: leetspeak, base64, look-alikes, Hinglish', 'Tool calls as structured args'], 'Engine down: the request is refused, never waved through'),
    ('3  Safety', 'orange', ['WildGuard 7B or Llama Guard (MLX)', '27 hard policy rules', 'Policy model: TF-IDF + logistic regression', 'Sanctions / consent lists'], 'WildGuard down: held for a person'),
    ('4  Risk', 'orange', ['Reversal cost: SQLAlchemy + SQLite graph', 'Impact model: TF-IDF + logistic regression', 'Weighted score 0 to 10'], 'A model down: held for a person'),
    ('5  Authorize', 'purple', ['ACL: how capable', 'AAL: autonomy earned this session (kept on the server)', 'min(ACL, AAL) vs the level needed'], 'New session starts at 6, never below 1.5'),
    ('6  Decide', 'purple', ['Review and block thresholds', 'Trajectory risk over the session', 'Fail closed if a check did not run', 'ALLOW / REVIEW / BLOCK'], 'Request not understood: asked to rephrase'),
    ('7  Escalate', 'green', ['Reviewer sign-in (PIN, PBKDF2)', 'Reviewer is not the requester', 'Agent model (Groq) writes the what-if', 'Held items run only on approval'], 'No reviewer signed in: stays held'),
    ('8  Ledger', 'green', ['SHA-256 chain + HMAC signature', 'Checkpoints outside the project', 'Personal data masked', 'Verify and replay'], 'Tampering: verify names the first bad row'),
]
SW, SG = 1.43, 0.087
for i, (name, col, its, fail) in enumerate(stages):
    x = 0.64 + i * (SW + SG); b, bg = PAL[col]
    hd = shp(MSO_SHAPE.PENTAGON, x, 1.4, SW, 0.46, b, b, 0.5)
    hd.text_frame.margin_left = Inches(0.08); hd.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    r = hd.text_frame.paragraphs[0].add_run(); r.text = name; r.font.size = Pt(11); r.font.bold = True; r.font.color.rgb = RGBColor(255, 255, 255)
    shp(MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.95, SW, 2.25, b, bg, 1.2)
    txt(x + 0.08, 2.03, SW - 0.14, 2.1, its, 9, INK, bullet=True, after=4)
    shp(MSO_SHAPE.ROUNDED_RECTANGLE, x, 4.3, SW, 0.85, 'E8B4AE', 'FDF3F2', 0.9)
    txt(x + 0.08, 4.34, SW - 0.14, 0.2, ['IF IT FAILS'], 7.5, 'B42318', bold=True, after=1)
    txt(x + 0.08, 4.52, SW - 0.14, 0.62, [fail], 8, '5A2A27')
layers = [('Presentation', 'Browser, Chart.js, Web Speech'), ('Orchestration', 'Python 3.12, sessions, sign-in'), ('Decision engine', 'Node.js (engine.js), deterministic'), ('Models', 'MLX (WildGuard, Llama Guard), Groq (agent), scikit-learn'), ('Data', 'SQLite, CSV lists, JSONL ledger'), ('Security', 'localhost only, Host / Origin checks, owner-only files')]
txt(0.64, 5.27, 4, 0.2, ['LAYERS UNDER THE PIPELINE'], 8.5, '5417A8', bold=True)
LW = (12.05 - 5 * 0.1) / 6
for i, (h, d) in enumerate(layers):
    x = 0.64 + i * (LW + 0.1)
    shp(MSO_SHAPE.ROUNDED_RECTANGLE, x, 5.52, LW, 0.8, 'DCE5EC', 'FFFFFF', 1.0)
    txt(x + 0.1, 5.58, LW - 0.18, 0.2, [h], 9.5, '5417A8', bold=True, after=2)
    txt(x + 0.1, 5.82, LW - 0.18, 0.5, [d], 8.5, '404040')
notes(new, 'Optional detail slide for questions: each stage, its technology, and what it does when it fails. The failure column is the engineering-depth answer.')
# move it right after the architecture slide
lst = prs.slides._sldIdLst; ids = list(lst); moved = ids[-1]; lst.remove(moved); lst.insert(5, moved)
# ---------------------------------------------------------------- extra slide: how VerifAI compares with existing tools
new = prs.slides.add_slide(src.slide_layout)
for nm in ('Text 0', 'Text 3', 'Slide Number Placeholder 0'):
    new.shapes._spTree.append(copy.deepcopy(named(src, nm)._element))
settext(named(new, 'Text 0'), 'What We Bring to the Table')
settext(named(new, 'Text 3'), 'SHARED WITH SOLUTION')
def pill(x, w, label, col):
    b, bg = PAL[col]
    sp = shp(MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.12, w, 0.5, b, 'FFFFFF', 2.0); sp.adjustments[0] = 0.5
    sp.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    para = sp.text_frame.paragraphs[0]; para.alignment = PP_ALIGN.CENTER
    r = para.add_run(); r.text = label; r.font.size = Pt(20); r.font.bold = True; r.font.color.rgb = RGBColor.from_string(b)
pill(1.9, 2.4, 'COMPARE', 'blue'); pill(9.0, 2.4, 'CONQUER', 'green')
rows = [
    ('Existing tools', 'Key limitation', 'How VerifAI addresses it'),
    ('Safety classifiers (Llama Guard, WildGuard)', 'Say whether text is harmful. They do not say whether an action can be undone, or whether the agent may do it', 'A safety model is one signal. Reversal cost, earned autonomy and 27 hard rules decide'),
    ('NeMo Guardrails', 'Flexible, but the team writes and maintains the rails (Colang) and the tool-call policy', 'Ready-made: 27 rules, a risk model, an approval flow and a tool-call gateway'),
    ('Guardrails AI', 'Validates the shape and content of model output. It does not decide whether an action is allowed', 'Judges the action itself: tool arguments, session history, limits'),
    ('Cloud filters (Bedrock Guardrails, Azure Prompt Shields)', 'Screen text for harm, topics, PII and prompt attacks. Text goes to the cloud, with no undo score or sign-off', 'Safety check runs on-device. Human approval (maker is not checker) and a signed ledger'),
]
colw = [1.75, 2.75, 2.55]; tx, ty = 0.64, 1.85
gs = new.shapes.add_table(len(rows), 3, Inches(tx), Inches(ty), Inches(sum(colw)), Inches(4.0))
tbl = gs.table
tblPr = gs._element.graphic.graphicData.tbl.tblPr; tblPr.set('firstRow', '0'); tblPr.set('bandRow', '0')
for sid in tblPr.findall(qn('a:tableStyleId')): tblPr.remove(sid)
for i, w in enumerate(colw): tbl.columns[i].width = Inches(w)
def border(cell, color='D5DEE6', w=9525):
    tcPr = cell._tc.get_or_add_tcPr()
    for tag in ('a:lnL', 'a:lnR', 'a:lnT', 'a:lnB'):
        ln = etree.SubElement(tcPr, qn(tag)); ln.set('w', str(w))
        sf = etree.SubElement(ln, qn('a:solidFill')); etree.SubElement(sf, qn('a:srgbClr')).set('val', color)
for ri, row in enumerate(rows):
    tbl.rows[ri].height = Inches(0.42 if ri == 0 else 0.89)
    for ci, text_ in enumerate(row):
        c = tbl.cell(ri, ci); c.margin_left = c.margin_right = Inches(0.09); c.margin_top = c.margin_bottom = Inches(0.05)
        c.vertical_anchor = MSO_ANCHOR.MIDDLE
        c.fill.solid(); c.fill.fore_color.rgb = RGBColor.from_string('EAF7FD' if ri == 0 else ('F3EEFC' if ci == 0 else ('EFF7E9' if ci == 2 else 'FFFFFF')))
        tf = c.text_frame; tf.word_wrap = True
        r = tf.paragraphs[0].add_run(); r.text = text_
        r.font.size = Pt(11 if ri == 0 else 9.5); r.font.bold = (ri == 0 or ci == 0)
        r.font.color.rgb = RGBColor.from_string('0B5E8C' if ri == 0 else INK)
        border(c)
# before / after panel
shp(MSO_SHAPE.ROUNDED_RECTANGLE, 7.75, 1.85, 4.95, 1.55, 'DCE5EC', 'F4F6F8', 1.0)
txt(7.92, 1.93, 4.6, 0.2, ['BEFORE'], 8, '8A96A3', bold=True)
txt(7.92, 2.15, 4.6, 0.6, ['Every agent action is a leap of faith.'], 15, '3A4756', bold=True)
shp(MSO_SHAPE.ROUNDED_RECTANGLE, 7.92, 2.78, 4.6, 0.5, 'E1E6EC', 'FFFFFF', 0.75)
txt(8.02, 2.84, 3.1, 0.4, ['“Delete all customer backups tonight”'], 9.5, '404040')
pl = shp(MSO_SHAPE.ROUNDED_RECTANGLE, 11.2, 2.88, 1.2, 0.3, 'C9D2DB', 'EEF1F4', 0.75)
txt(11.25, 2.92, 1.1, 0.25, ['Keyword filter: pass'], 7.5, '5B6573')
shp(MSO_SHAPE.ROUNDED_RECTANGLE, 7.75, 3.55, 4.95, 2.45, 'DCE5EC', 'FFFFFF', 1.0)
txt(7.92, 3.63, 4.6, 0.2, ['AFTER VERIFAI'], 8, '007CC3', bold=True)
txt(7.92, 3.85, 4.6, 0.6, ['Every action gets a decision, a reason and a record.'], 14, INK, bold=True)
shp(MSO_SHAPE.ROUNDED_RECTANGLE, 7.92, 4.5, 4.6, 0.62, 'F1C9C4', 'FDF3F2', 1.0)
txt(8.02, 4.55, 4.4, 0.55, ['BLOCKED  ·  no_backup_destruction', 'Backups are the way back from any mistake. Reversal cost: unbounded.'], 9, '5A2A27', after=1)
shp(MSO_SHAPE.ROUNDED_RECTANGLE, 7.92, 5.22, 2.25, 0.62, 'DCE5EC', 'F8FAFC', 0.75)
txt(8.0, 5.26, 2.1, 0.55, ['DECIDED ON', 'The server, by rules'], 8.5, '404040', after=1)
shp(MSO_SHAPE.ROUNDED_RECTANGLE, 10.27, 5.22, 2.25, 0.62, 'DCE5EC', 'F8FAFC', 0.75)
txt(10.35, 5.26, 2.1, 0.55, ['DATA LEAVES THE MACHINE', 'Only agent text, to Groq'], 8.5, '404040', after=1)
txt(0.64, 5.98, 7.0, 0.35, ['Comparison is based on each tool\'s public description. VerifAI is a prototype and has not yet been tested by outside people.'], 8, '7A8793')
notes(new, 'Say it in one line: the other tools judge text or the shape of an output; VerifAI judges the action: can it be undone, is the agent allowed, and who approved it. Be fair: they are good at what they do, and they can be used together with VerifAI. Sources for the comparison: premai.io blog "Production LLM guardrails: NeMo, Guardrails AI, Llama Guard compared"; AWS Bedrock Guardrails documentation; Microsoft Learn, Azure AI Content Safety Prompt Shields. Honest limit: VerifAI is a prototype and no outside tester has scored it yet.')
lst = prs.slides._sldIdLst; ids = list(lst); moved2 = ids[-1]; lst.remove(moved2); lst.insert(4, moved2)

# the template prints static slide numbers: renumber
for n, sl in enumerate(prs.slides, 1):
    for sh in sl.shapes:
        if sh.name.startswith('Slide Number') and sh.has_text_frame:
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if r.text.strip().isdigit(): r.text = str(n)
prs.save(OUT)
print('wrote', OUT)
for w in warn: print('WARN', w)
