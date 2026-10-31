#!/usr/bin/env node
/* Helper for measure_wildguard.py.   node tests/eval_conditions.js seen|verdicts < input.json > output.json
   seen:     [{id, text}]  ->  [{id, seen}]   the text the server would show the models (original + plain readings)
   verdicts: [{id, text, wg, policy, backend}] -> [{id, A, B, C, D}] verdicts with the engine only, + policy model, + WildGuard, + both */
global.window = global;
const V = require('../engine.js') && global.VerifAI;
const ctx = { agent: 'agent-01', status: 'ACTIVE', snapshot: true };
let buf = ''; process.stdin.on('data', d => buf += d); process.stdin.on('end', () => {
  const items = JSON.parse(buf), mode = process.argv[2];
  if (mode === 'seen') {
    console.log(JSON.stringify(items.map(i => { const v = V.normalizeText(i.text); return { id: i.id, seen: v.length ? i.text + ' || plain reading: ' + v.join(' || ').slice(0, 1500) : i.text }; })));
    return;
  }
  const run = (i, wg, pol) => {
    const a = V.fromPrompt(i.text, ctx);
    if (wg && i.wg && i.wg.ok) a.wg = i.wg;
    if (pol && i.policy && i.policy.ok) a.policyCheck = i.policy;
    a.backend = a.cat === 'read' ? { ok: false, skipped: 'read-only' } : (i.backend && i.backend.ok ? i.backend : { ok: false });
    a.degraded = [];
    const r = V.evaluate(a, V.DEFAULT_CFG, []);
    return { v: r.verdict, hard: r.policy.hard.map(h => h.id).join(',') };
  };
  console.log(JSON.stringify(items.map(i => ({ id: i.id, A: run(i, false, false), B: run(i, false, true), C: run(i, true, false), D: run(i, true, true) }))));
});
