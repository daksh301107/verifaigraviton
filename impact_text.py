"""Text preparation for the impact model (shared by training and the server, because the trained pipeline refers to it).

TF-IDF cannot tell "₹5000" from "₹80 lakh", or "5 people" from "50,000 users". This adds a few plain words that
describe the SIZE of any amount or count, and whether the request covers "all/everything". The original text is kept.
"""
import re

_MULT = {'k': 1e3, 'thousand': 1e3, 'l': 1e5, 'lac': 1e5, 'lacs': 1e5, 'lakh': 1e5, 'lakhs': 1e5, 'cr': 1e7, 'crore': 1e7, 'crores': 1e7,
         'm': 1e6, 'mn': 1e6, 'million': 1e6, 'b': 1e9, 'bn': 1e9, 'billion': 1e9}
_NUM = r'(\d[\d,]*(?:\.\d+)?)'
_UNIT = r'(k|thousand|lakhs?|lacs?|crores?|cr|million|mn|billion|bn|m|l)'
_MONEY_SYM = re.compile(r'(?:₹|rs\.?\s*|inr\s*|\$|usd\s*|rupees?\s*)\s*' + _NUM + r'\s*' + _UNIT + r'?\b', re.I)
_MONEY_UNIT = re.compile(r'\b' + _NUM + r'\s*' + _UNIT + r'\b(?!\w)', re.I)
_COUNT = re.compile(r'\b' + _NUM + r'\s+(?:[a-z]+\s+){0,2}?(recipients?|users?|customers?|employees?|records?|rows?|logs?|emails?|people|subscribers?|accounts?|orders?|candidates?|patients?|members?|clients?|tickets?|files?|entries|devices|servers?)\b', re.I)
_ALL = re.compile(r'\b(all|every|everyone|everybody|everything|entire|whole)\b', re.I)


def _val(num, unit):
    try:
        v = float(num.replace(',', ''))
    except ValueError:
        return None
    return v * _MULT.get((unit or '').lower(), 1)


def prep(text):
    t = str(text)
    toks = []
    vals = [_val(m.group(1), m.group(2)) for m in _MONEY_SYM.finditer(t)]
    vals += [_val(m.group(1), m.group(2)) for m in _MONEY_UNIT.finditer(t) if m.group(2).lower() in ('k', 'thousand', 'lakh', 'lakhs', 'lac', 'lacs', 'crore', 'crores', 'cr', 'million', 'mn', 'billion', 'bn')]
    vals = [v for v in vals if v]
    if vals:
        v = max(vals)
        toks.append('zzamt_' + ('tiny' if v < 1e3 else 'small' if v < 1e4 else 'mid' if v < 5e4 else 'large' if v < 5e5 else 'huge' if v < 1e7 else 'massive'))
    cs = [_val(m.group(1), None) for m in _COUNT.finditer(t)]
    cs = [c for c in cs if c]
    if cs:
        c = max(cs)
        toks.append('zzcnt_' + ('few' if c < 10 else 'dozens' if c < 100 else 'hundreds' if c < 1000 else 'thousands'))
    if _ALL.search(t):
        toks.append('zzscope_all')
    return t + ' ' + ' '.join(toks) if toks else t


def prep_all(texts):
    return [prep(t) for t in texts]
