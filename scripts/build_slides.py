#!/usr/bin/env python3
"""Builds VerifAI_Architecture_Slides.pptx: (1) the "Architecture & Technical Design" slide in the hackathon template's
layout, filled in, and (2) the technology pipeline stage by stage.   python3 scripts/build_slides.py
Paste the slides into your event template deck (Reuse Formatting) to get its logos and footer; they are not drawn here."""
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'VerifAI_Architecture_Slides.pptx')
rgb = lambda h: RGBColor.from_string(h)
C = {'blue': ('2E75B6', 'EAF4FC'), 'purple': ('5B2DA0', 'F2EEFA'), 'orange': ('E8923C', 'FDF4E8'), 'green': ('6AA84F', 'EFF6E8')}
INK, GRAY = '262626', '595959'
prs = Presentation(); prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
blank = prs.slide_layouts[6]

def text(slide, x, y, w, h, paras, anchor=MSO_ANCHOR.TOP, margin=0.0):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf = tb.text_frame; tf.word_wrap = True
    tf.vertical_anchor = anchor
    for m in ('margin_left', 'margin_right', 'margin_top', 'margin_bottom'): setattr(tf, m, Inches(margin))
    fill(tf, paras); return tb

def fill(tf, paras):
    """paras: list of (text, size, color, bold, italic, align, font, space_after)"""
    for i, p in enumerate(paras):
        t, size, color, bold, italic, align, font, after = (list(p) + [None] * 8)[:8]
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align or PP_ALIGN.LEFT
        if after is not None: para.space_after = Pt(after)
        r = para.add_run(); r.text = t
        r.font.size = Pt(size); r.font.bold = bool(bold); r.font.italic = bool(italic)
        r.font.name = font or 'Arial'; r.font.color.rgb = rgb(color or INK)

def box(slide, x, y, w, h, border, bg='FFFFFF', width=1.75, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.04):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE: s.adjustments[0] = radius
    s.fill.solid(); s.fill.fore_color.rgb = rgb(bg); s.line.color.rgb = rgb(border); s.line.width = Pt(width); s.shadow.inherit = False
    return s

def titles(slide, title, sub):
    text(slide, 0.5, 0.28, 10.5, 0.6, [(title, 30, '1F1F1F', False, False, None, 'Georgia')])
    text(slide, 0.5, 0.9, 11.5, 0.4, [(sub, 13.5, GRAY)])

def bullets(items, size=10.5, color=INK, after=3):
    return [('•  ' + t, size, color, False, False, PP_ALIGN.LEFT, None, after) for t in items]

# ------------------------------------------------------------------ slide 1: the template page, filled in
s = prs.slides.add_slide(blank)
titles(s, 'Architecture & Technical Design', 'VerifAI judges every AI action on the server before it runs. Read left to right: person, logic, models, outcome.')
L, W, GAP = 0.64, 2.64, 0.5
tops = [
    ('blue', 'USER / DEVICE', ['Browser page: HTML, CSS, plain JavaScript', 'Typed prompts or voice (Web Speech API)', 'Files: txt, md, csv, json, docx, pdf, png, jpg', 'Scanned pages read offline (Tesseract.js)', 'Sign-in for reviewers and admins']),
    ('purple', 'APPLICATION / LOGIC', ['Python 3.12 server, localhost only', 'Decision engine in a Node.js worker', '27 hard policy rules + text normaliser', 'Risk score, then authorisation min(AAL, ACL)', 'Human approval: reviewer is never the requester', 'Tool gateway in front of a sandbox database']),
    ('orange', 'AI / DATA LAYER', ['WildGuard 7B: safety check, on-device (MLX)', 'Llama 3.1 8B Instruct: the agent, on-device', 'scikit-learn: impact and policy-rule models', 'SQLAlchemy + SQLite: reversal-cost graph', 'Sanctions and consent lists (CSV)']),
    ('green', 'OUTPUT / ACTION', ['ALLOW / REVIEW / BLOCK, with the reason in plain words', 'Edited document, or a tool run (sandbox)', 'Dashboard (Chart.js), what-if, downloadable report', 'Signed, hash-chained evidence ledger', 'Answers read aloud']),
]
for i, (col, head, items) in enumerate(tops):
    x = L + i * (W + GAP); b, bg = C[col]
    box(s, x, 1.45, W, 2.5, b, bg, 1.75)
    text(s, x + 0.12, 1.55, W - 0.24, 0.3, [(head, 12, b, True, False, PP_ALIGN.CENTER)])
    text(s, x + 0.14, 1.92, W - 0.26, 2.0, bullets(items, 10))
    if i < 3:
        ch = s.shapes.add_shape(MSO_SHAPE.CHEVRON, Inches(x + W + 0.09), Inches(2.45), Inches(0.32), Inches(0.5))
        ch.fill.solid(); ch.fill.fore_color.rgb = rgb('C5CCD6'); ch.line.fill.background()
lows = [
    ('2E75B6', 'TECHNOLOGY CHOICES', ['Languages: Python, JavaScript (browser and Node.js)', 'Models: WildGuard 7B, Llama 3.1 8B, TF-IDF + logistic regression', 'Libraries: scikit-learn, SQLAlchemy, Apple MLX, Chart.js, pdf.js, JSZip', 'Cloud: none needed (optional hosted LLM key)', 'Hardware: one 16 GB MacBook']),
    ('4B1FA0', 'ENGINEERING RATIONALE', ['On-device: no data leaves the machine, no API key', 'Rules decide, models advise: same input, same verdict', 'Engine on the server, so the page cannot change a verdict', 'Plain standard library: little to audit', 'Trade-off: local 8B models are slower and plainer']),
    ('5F9A3F', 'VALIDATION / RELIABILITY', ['305 automated checks (211 engine, 66 server, 28 browser)', 'Disguised attacks blocked: 9% to 100% (leetspeak)', 'Policy model flagged 62 of 63 unseen rewordings', 'Impact model right 93-97% on our test sets', 'A check that cannot run means a person decides', 'Edited or deleted ledger rows are detected']),
]
LW = (12.05 - 0.4) / 3
for i, (col, head, items) in enumerate(lows):
    x = L + i * (LW + 0.2)
    box(s, x, 4.12, LW, 1.78, 'DCE1E8', 'FFFFFF', 1.0)
    text(s, x + 0.16, 4.2, LW - 0.3, 0.25, [(head, 10.5, col, True)])
    text(s, x + 0.16, 4.5, LW - 0.3, 1.4, bullets(items, 9.5, '404040', 2))
box(s, 0.64, 6.05, 12.05, 1.2, 'E3E7EE', 'F8F9FC', 1.0)
bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.64), Inches(6.05), Inches(0.09), Inches(1.2)); bar.fill.solid(); bar.fill.fore_color.rgb = rgb('4B1FA0'); bar.line.fill.background()
text(s, 0.95, 6.1, 11.6, 0.25, [('WHAT TO SAY', 10.5, '4B1FA0', True)])
text(s, 0.95, 6.38, 11.6, 0.9, [
    ('•  A person types, speaks or uploads. The server judges it before anything runs. Two on-device models add judgement. Out comes a decision, a plain-language reason and a signed record.', 10.5, INK, False, False, None, None, 3),
    ('•  The key design decision: rules decide and models only advise, and if a check cannot run the answer is “ask a person”, never “allow”.', 10.5, INK, False, False, None, None, 3),
    ('•  Depth: attacks are disguised (9% to 100% caught), the ledger resists tampering, and we say what we have not measured: outside testers.', 10.5, INK, False, False, None, None, 3)])

# ------------------------------------------------------------------ slide 2: technology pipeline, stage by stage
s = prs.slides.add_slide(blank)
titles(s, 'Technology pipeline, stage by stage', 'Each stage of the guardrail pipeline, and what it is built with. Colours match the architecture slide.')
stages = [
    ('1  Input', 'blue', ['Browser page: HTML, CSS, JS', 'Typed or spoken (Web Speech)', 'Files: pdf.js, JSZip, Tesseract OCR', 'Session id issued by the server']),
    ('2  Analyze', 'purple', ['Node.js worker runs engine.js', 'Action Analyzer: rules + typo repair', 'Text normaliser: leetspeak, base64, look-alikes, Hinglish', 'Tool calls arrive as structured arguments']),
    ('3  Safety', 'orange', ['WildGuard 7B (MLX, on-device)', '27 hard policy rules', 'Policy model: TF-IDF + logistic regression', 'Sanctions / consent lists (CSV)']),
    ('4  Risk', 'orange', ['Reversal cost: SQLAlchemy + SQLite foreign-key graph', 'Impact model: TF-IDF + logistic regression', 'Weighted score 0 to 10']),
    ('5  Authorize', 'purple', ['ACL: how capable for this action', 'AAL: autonomy earned from session history, kept on the server', 'Needs min(ACL, AAL) to reach the level']),
    ('6  Decide', 'purple', ['Review and block thresholds', 'Trajectory risk over the session', 'Fail closed if a check did not run', 'ALLOW / REVIEW / BLOCK']),
    ('7  Escalate', 'green', ['Reviewer sign-in (PIN, PBKDF2)', 'Reviewer is not the requester', 'Llama 3.1 8B writes the what-if', 'Held answer or tool call runs only on approval']),
    ('8  Ledger', 'green', ['SHA-256 chain + HMAC signature', 'Checkpoints outside the project folder', 'Personal data masked', 'Verify and replay']),
]
fails = ['Server unreachable: nothing is checked or run, and the page says so', 'Engine down: the request is refused, never waved through', 'WildGuard down: a non-read-only action is held for a person', 'A model down: held for a person, and the reason is shown', 'New session starts at autonomy 6, never below 1.5', 'Request not understood: asked to rephrase, not guessed', 'No reviewer signed in: the action stays held', 'Tampering: verify names the first bad row']
SW, SG = 1.43, 0.087
for i, (name, col, items) in enumerate(stages):
    x = 0.64 + i * (SW + SG); b, bg = C[col]
    hd = s.shapes.add_shape(MSO_SHAPE.PENTAGON, Inches(x), Inches(1.45), Inches(SW), Inches(0.5)); hd.fill.solid(); hd.fill.fore_color.rgb = rgb(b); hd.line.fill.background()
    hd.text_frame.margin_left = Inches(0.08); hd.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    fill(hd.text_frame, [(name, 11.5, 'FFFFFF', True)])
    box(s, x, 2.05, SW, 2.5, b, bg, 1.25)
    text(s, x + 0.08, 2.13, SW - 0.14, 2.4, bullets(items, 9.5, INK, 4))
    box(s, x, 4.65, SW, 0.98, 'F1C9C4', 'FDF3F2', 1.0)
    text(s, x + 0.08, 4.69, SW - 0.14, 0.92, [('IF IT FAILS', 8, 'B42318', True, False, None, None, 1), (fails[i], 8.5, '5A2A27')])
layers = [('Presentation', 'Browser, Chart.js, Web Speech'), ('Orchestration', 'Python 3.12 http.server, sessions, auth'), ('Decision engine', 'Node.js (engine.js), deterministic'), ('Models', 'MLX (WildGuard, Llama), scikit-learn'), ('Data', 'SQLite, CSV lists, JSONL ledger'), ('Security', 'localhost only, Host / Origin checks, owner-only files')]
text(s, 0.64, 5.78, 4, 0.25, [('LAYERS UNDER THE PIPELINE', 10, '4B1FA0', True)])
LWID = (12.05 - 5 * 0.1) / 6
for i, (h, d) in enumerate(layers):
    x = 0.64 + i * (LWID + 0.1)
    box(s, x, 6.07, LWID, 1.0, 'DCE1E8', 'FFFFFF', 1.0)
    text(s, x + 0.1, 6.13, LWID - 0.18, 0.9, [(h, 10, '4B1FA0', True, False, None, None, 2), (d, 9, '404040')])
prs.save(OUT); print('wrote', OUT)
