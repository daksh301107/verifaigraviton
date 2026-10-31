#!/usr/bin/env node
/* Runs the decision engine (engine.js, docs.js) inside the server, so verdicts are produced where a user
   cannot edit them. server.py starts this process and sends one JSON request per line on stdin; it answers
   with one JSON line on stdout. The server does the slow lookups (WildGuard, models, lists) and hands the
   results in; this process only decides. */
global.window = global;
const fs = require('fs'), path = require('path');
require('./engine.js');
require('./docs.js');
const V = global.VerifAI, D = global.VerifAIDocs;
const clone = o => JSON.parse(JSON.stringify(o));

// The reversal graph is read from the database by the server and pushed in here.
function setSchema(p) {
  global.fetch = async () => ({ status: 200, json: async () => p });
  return V.loadSchema().then(ok => ({ ok }));
}

// What the browser's enrich() used to do, but with the lookups supplied by the server.
function attach(a, inp) {
  if (inp.wg) a.wg = inp.wg;
  if (inp.policy) a.policyCheck = inp.policy;
  if (a.cat === 'read') a.backend = { ok: false, skipped: 'read-only request, model not consulted' };
  else if (inp.backend && inp.backend.ok) a.backend = { ok: true, impact: inp.backend.impact, intent_match: inp.backend.intent_match, confidence: inp.backend.confidence, probs: inp.backend.probs || null, source: inp.backend.source };
  else a.backend = { ok: false };
  a.degraded = inp.degraded || [];
  return a;
}

const ops = {
  setSchema,
  evaluate(inp) {
    const a = attach(inp.structured ? V.fromStructured(inp.structured, inp.ctx) : V.fromPrompt(inp.text, inp.ctx), inp);
    const hist = inp.noSession ? [] : (inp.hist || []);
    const res = V.evaluate(a, inp.cfg || V.DEFAULT_CFG, hist);
    return { res, hist };
  },
  // Documents: the browser plans the edits; every edit is checked here, one by one, on its own text.
  docEvaluate(inp) {
    const doc = inp.doc, pl = inp.plan;
    // per-edit text is judged on its own; only the request-level lookups and the fail-closed flag carry over
    const hook = (a, op) => { if (inp.flagged && op && inp.flagged[op.block] !== undefined && op.kind !== 'redact') a.injection = true;
      if (inp.wg) a.wg = inp.wg; if (inp.policy) a.policyCheck = inp.policy; a.degraded = inp.degraded || []; };
    const results = D.evaluateAll(pl, doc, inp.ctx, inp.cfg || V.DEFAULT_CFG, inp.hist || [], hook);
    const rank = { BLOCK: 3, REVIEW: 2, ALLOW: 1 };
    const worst = results.slice().sort((x, y) => rank[y.res.verdict] - rank[x.res.verdict] || y.res.score - x.res.score)[0];
    const res = worst ? worst.res : V.evaluate(Object.assign(V.fromPrompt('Preview and review the document ' + doc.name, inp.ctx), { degraded: inp.degraded || [] }), inp.cfg || V.DEFAULT_CFG, inp.hist || []);
    return { results, res };
  },
  defaults() { return { cfg: clone(V.DEFAULT_CFG), policy: clone(V.POLICY_DEFAULTS) }; },
  normalize(inp) { return { variants: V.normalizeText(inp.text) }; },
  describe(inp) { return { raw: V.fromStructured(inp.structured, inp.ctx).raw }; },
  screen(inp) { return V.screenOutput(inp.text); },
  policyInfo() { return { rules: V.HARD_RULES.length, policy: V.POLICY }; },
  setPolicy(p) { Object.assign(V.POLICY, p); return { policy: V.POLICY }; },
  ping() { return { ok: true }; },
};

let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1);
    if (!line.trim()) continue;
    let req;
    try { req = JSON.parse(line); } catch (e) { continue; }
    Promise.resolve().then(() => ops[req.op](req.args || {})).then(
      r => process.stdout.write(JSON.stringify({ id: req.id, ok: true, result: r }) + '\n'),
      e => process.stdout.write(JSON.stringify({ id: req.id, ok: false, error: String(e && e.stack || e) }) + '\n'));
  }
});
process.stdin.on('end', () => process.exit(0));
