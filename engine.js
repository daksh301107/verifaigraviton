/* VerifAI — client-side simulation of the approved pipeline (pipeline_v3).
   Deterministic: same action + config + session history -> same output.
   WildGuard (7B) is NOT called; a keyword heuristic stands in for it. */
(function (root) {
  'use strict';

  const LEVEL_NUM = { INTERN: 2.5, JUNIOR: 4.5, SENIOR: 6.5, PRINCIPAL: 8.5 };   // only used to read the legacy test-case labels

  // type -> display verb, category, base impact (0-10), required autonomy level (0-10)
  const ACTIONS = {
    update_field:          { verb: 'UPDATE',  cat: 'update', base: 2, req: 3 },
    create_record:         { verb: 'CREATE',  cat: 'create', base: 1, req: 3 },
    create_calendar_event: { verb: 'CREATE',  cat: 'create', base: 1, req: 3 },
    delete_record:         { verb: 'DELETE',  cat: 'delete', base: 5, req: 5.5 },
    bulk_delete:           { verb: 'DELETE*', cat: 'delete', base: 7, req: 8 },
    cancel_subscription:   { verb: 'CANCEL',  cat: 'update', base: 3, req: 5 },
    process_refund:        { verb: 'REFUND',  cat: 'money',  base: 6, req: 5.5 },
    approve_payment:       { verb: 'APPROVE', cat: 'money',  base: 4, req: 5.5 },
    transfer_funds:        { verb: 'TRANSFER',cat: 'money',  base: 6, req: 8 },
    send_bulk_email:       { verb: 'SEND',    cat: 'comms',  base: 6, req: 5.5 },
    send_email:            { verb: 'SEND',    cat: 'comms',  base: 3, req: 4.5 },
    change_permissions:    { verb: 'GRANT',   cat: 'update', base: 5, req: 6.5 },
    edit_document:         { verb: 'EDIT',    cat: 'update', base: 3, req: 5 },
    generate_report_preview:{ verb: 'READ',   cat: 'read',   base: 0, req: 1 },
    read_data:             { verb: 'READ',    cat: 'read',   base: 0, req: 1 },
    edit_financial_figure: { verb: 'EDIT₹',  cat: 'money',  base: 4, req: 5.5 },
    export_data:           { verb: 'EXPORT',  cat: 'export', base: 5, req: 5.5 },
    _unknown:              { verb: 'CALL',    cat: 'other',  base: 4, req: 5.5 },
  };

  // Demo schema from reversal_cost_engine/schema.py + EDGE_HEALS_ON_RESTORE
  const GRAPH = {
    edges: [
      { child: 'orders',        parent: 'users',         heals: true  },
      { child: 'payments',      parent: 'orders',        heals: false },
      { child: 'reviews',       parent: 'products',      heals: true  },
      { child: 'subscriptions', parent: 'users',         heals: true  },
      { child: 'invoices',      parent: 'subscriptions', heals: false },
    ],
    // seeded rows hanging off each parent (table-level multiplicity)
    rows: {
      'users>orders': 2, 'users>subscriptions': 1, 'orders>payments': 2,
      'subscriptions>invoices': 1, 'products>reviews': 3,
    },
  };

  /* ---------- Hard policy pack ----------
     A hard rule forbids an action outright: it short-circuits to BLOCK before any scoring, whatever the
     risk score or the agent's autonomy. Each rule has a group, the principle it protects and (where one
     exists) a safe alternative. The full list with rationale is in HARD_POLICY.md. For edits inside a
     document only the instruction is matched, not the document's own text, so a letter that merely
     contains the word "password" is not blocked. */
  // Values a policy owner can change in Settings (changes are written to the ledger). The model thresholds
  // say how sure the policy model must be before it blocks on its own.
  // wgUncorroborated: what a WildGuard flag does when nothing else agrees with it: 1 = nothing, 2 = adds risk points, 3 = holds the request for a person.
  const POLICY = { singleTxnCap: 10000000, dualApproval: 10000, modelThreshold: 0.95, modelThresholdWithWG: 0.6, modelReview: 0.5, wgUncorroborated: 3 };
  const POLICY_DEFAULTS = Object.assign({}, POLICY);
  const inr = n => '₹' + Number(n).toLocaleString('en-IN');
  // things you can "kill", "shoot" or "blast" without harming anyone: used by the violence rule and to judge a safety-model flag
  const TECH_OBJ = /^(a |the |my |our )?(\w+ ){0,2}(process|processes|job|jobs|task|thread|server|session|app|bug|bugs|time|switch|port|pid|signal|cron|container|pod|query|queries|connection|batch|script|daemon|service|workflow|pipeline|performance|lights?|goal|photo|photos|video|scene|footage|option|options|contract|subscription|account|employee|employees|mood|vibe|idea|ideas|rumou?r|fire|candle|email|e-mail|message|sms|campaign|speakers?|music|playlist|exam|test|interview|presentation|pitch)\b/;
  const VIOLENT_VERB = /\b(kill|murder|stab|shoot|poison|bomb|blast|blow up|burn down|massacre|behead|kidnap|assassinat|strangl|torture|rape)\w*\s+(.{0,40})/g;
  function violentWording(t) { let m; VIOLENT_VERB.lastIndex = 0; while ((m = VIOLENT_VERB.exec(t))) { if (!TECH_OBJ.test(m[2])) return true; } return false; }
  const ut = a => (a.source === 'doc' ? String(a.raw || '') : String(a.text || '')).toLowerCase();
  const NO = { possible: false, steps: [] };
  const alt = (...steps) => ({ possible: true, steps });
  const HARD_RULES = [
    // ---- A. Identity and official documents ----
    { id: 'no_gov_id_edit', group: 'Identity & documents', basis: 'Government IDs are legal identity evidence; an agent never alters them.',
      title: 'Government ID documents are never edited by agents',
      test: a => a.govId, remediation: a => NO },
    { id: 'id_like_image_edit', group: 'Identity & documents', basis: 'Images that look like an ID need verified provenance.',
      title: 'Images flagged as ID-like need verified re-upload',
      test: a => a.idLike && !a.govId,
      remediation: a => alt('Ask the user to upload a fresh photo through the verified profile flow.',
        'Re-submit the edit once the image no longer matches the ID-like classifier.') },
    { id: 'no_official_document', group: 'Identity & documents', basis: 'Job, visa and loan fraud rely on credential documents produced outside the issuer.',
      title: 'Official-looking documents (offer/appointment/experience letters, certificates, marksheets, ID cards, payslips, NOCs) are not generated for a real organisation, with or without the word "fake"',
      test: a => a.officialDocRequest,
      remediation: a => alt('I can produce a generic, clearly-labelled SAMPLE/TEMPLATE instead — placeholder names, no real company, and a "SAMPLE — not a real document" watermark.',
        'For an actual document, this has to go through that organisation\'s own official process — an agent should not generate it directly.') },
    { id: 'no_impersonation', group: 'Identity & documents', basis: 'Acting as someone else deceives the recipient and defeats accountability.',
      title: 'An agent does not impersonate a person, official or institution (CEO, bank, police, tax or government office)',
      test: a => /\b(impersonat\w*|pretend\w* (to be|you are)|pose as|posing as|sign (it |this )?as|write as if (you are|from)|pass (it )?off as)\b.{0,40}\b(ceo|cfo|director|manager|bank|police|officer|official|government|rbi|sebi|income tax|tax (office|department)|court|customer care|support team|hr|doctor|lawyer)\b/.test(ut(a)),
      remediation: a => alt('Send it under the agent\'s own name and say it is sent on behalf of the person.') },
    { id: 'no_kyc_bypass', group: 'Identity & documents', basis: 'KYC/AML checks are a legal precondition for onboarding and payments.',
      title: 'Identity verification (KYC/AML) is never skipped, waived or worked around',
      test: a => /\b(skip|bypass|waive|avoid|circumvent|get around|without|disable|turn off)\b.{0,25}\b(kyc|aml|identity (check|verification)|id verification|know your customer|due diligence)\b/.test(ut(a)),
      remediation: a => alt('Route the customer to the normal verification flow and continue once it is complete.') },

    // ---- B. Guardrail, audit and human oversight ----
    { id: 'no_audit_tamper', group: 'Guardrail & oversight', basis: 'The ledger is the evidence that the system behaved; an agent cannot erase it.',
      title: 'Audit logging cannot be disabled or erased by an agent',
      test: a => /(disable|turn off|delete|erase|wipe|truncate|clear|rewrite|alter|edit).{0,25}(audit|logging|logs? of|evidence|ledger)/.test(ut(a)) && !/older than|rotate|archive/.test(ut(a)),
      remediation: a => NO },
    { id: 'no_policy_override', group: 'Guardrail & oversight', basis: 'The agent cannot change the rules that govern the agent.',
      title: 'An agent cannot disable, weaken or override the guardrail, its thresholds or its policies',
      test: a => /\b(disable|turn off|switch off|deactivate|weaken|loosen|lower|override|bypass|circumvent|ignore|remove|get around)\b.{0,25}\b(guardrail|guard rails?|safety (check|filter|layer|net)|policy|policies|risk (engine|check|threshold)|threshold|content filter|verifai)\b/.test(ut(a)),
      remediation: a => alt('Ask a policy owner to change the rule through the Settings page, where the change is recorded.') },
    { id: 'no_privilege_escalation', group: 'Guardrail & oversight', basis: 'Autonomy is granted by people, never self-assigned.',
      title: 'An agent cannot raise its own permissions, autonomy level or access',
      test: a => /\b(grant|give|assign|raise|increase|elevate|escalate|upgrade|promote)\b.{0,20}\b(me|myself|this agent|the agent|my own|agent-\d+)\b.{0,25}\b(admin|root|superuser|full access|all permissions|more access|autonomy|privileges?|permissions?|level \d+)\b|\b(raise|increase|max(imi[sz]e)?) (my|the agent'?s?|its) (own )?(autonomy|privileges?|permissions?|authority)\b|\bmake me (an? )?(admin|root|superuser)\b/.test(ut(a)),
      remediation: a => alt('Request the permission from an administrator; it is granted outside the agent.') },
    { id: 'no_approval_bypass', group: 'Guardrail & oversight', basis: 'A held action needs a person; the agent cannot approve its own work around that.',
      title: 'Required human approval or review is never skipped or auto-approved',
      test: a => /\b(skip|bypass|without|avoid|circumvent|get around|disable|ignore)\b.{0,20}\b(human |manager |manual |second |dual )?(approval|review|sign.?off|authori[sz]ation|four.eyes|maker.checker)\b|\bauto.?approve (all|every|everything)\b|\bapprove (it|this|everything) (automatically|without)\b/.test(ut(a)),
      remediation: a => alt('Submit the action normally; a held action waits in the approval queue for a person.') },
    { id: 'no_self_approval', group: 'Guardrail & oversight', basis: 'Segregation of duties: nobody approves a payment, refund or claim for themselves.',
      title: 'An agent or user does not approve their own payment, expense, refund, claim or loan',
      test: a => /\b(approve|authori[sz]e|release|sanction|clear)\b.{0,15}\b(my own|my|own|myself|self|the agent'?s own)\b.{0,15}\b(payment|expense|refund|claim|invoice|loan|reimbursement|salary|bonus|advance)\b|\bpay (myself|me)\b|\b(refund|reimburse) (myself|me)\b/.test(ut(a)),
      remediation: a => alt('Route it to a different approver.') },
    { id: 'no_record_backdating', group: 'Guardrail & oversight', basis: 'Records must show when things actually happened.',
      title: 'Records, invoices and approvals are not back-dated, pre-dated or given a false timestamp',
      test: a => /\b(back.?dat\w*|pre.?dat\w*|post.?dat\w*|(change|alter|edit|set|modify|fake|falsif\w*)\b.{0,15}\b(the )?(date|timestamp|time stamp)\b.{0,20}\b(of|on|for)\b.{0,15}\b(invoice|record|contract|approval|entry|receipt|log|transaction|agreement))\b/.test(ut(a)),
      remediation: a => alt('Create a new dated entry that references the original and explains the correction.') },

    // ---- C. Money and financial integrity ----
    { id: 'no_structuring', group: 'Money & finance', basis: 'Splitting payments to stay under a limit is a recognised money-laundering pattern.',
      title: 'Payments are not split or timed to stay under an approval or reporting threshold',
      test: a => /\b(split|break|divide|spread|stagger|structure|chunk)\b.{0,50}\b(under|below|beneath|within|to (stay|keep|remain|avoid)|so (that )?(it|they) (stay|remain|don'?t|doesn'?t|is not|isn'?t))\b.{0,30}\b(limit|threshold|cap|reporting|approval|radar|detection|flag|10,?000|50,?000|1,?00,?000|lakh)\b|\b(avoid|evade|dodge|stay under|keep under|below) (the )?(reporting|approval|compliance|aml|str|ctr) (limit|threshold|requirement|check|flag)/.test(ut(a)),
      remediation: a => alt('Make one payment and send it through dual approval, or ask compliance to approve the schedule.') },
    { id: 'no_sanctions_evasion', group: 'Money & finance', basis: 'Dealing with sanctioned parties or untraceable channels is prohibited.',
      title: 'No payments to sanctioned or blacklisted parties, shell entities or untraceable channels (mixers, hawala)',
      test: a => /\b(sanction(ed|s)?|blacklist(ed)?|embargo(ed)?|watch.?list(ed)?|debarred|shell (company|firm|entity|account)|dummy (company|vendor|account)|ghost (vendor|employee)|crypto (mixer|tumbler)|mixer|tumbler|hawala|offshore (account|shell))\b/.test(ut(a)) && /\b(pay|payment|transfer|wire|send|remit|route|move|fund|disburse|onboard|add)\b/.test(ut(a)),
      remediation: a => alt('Stop and refer to compliance; do not proceed without a documented clearance.') },
    { id: 'no_off_books', group: 'Money & finance', basis: 'Books must reflect real transactions; hiding income or tax is fraud.',
      title: 'No off-the-books, double-booked or tax-evading entries (cash without invoice, two sets of books, fake expenses)',
      test: a => /\b(off the books|off.?book\w*|under the table|two sets of books|double books?|cook the books|cash (without|with no) (an )?invoice|without (an )?invoice to (save|avoid|evade)|(evade|avoid|dodge|hide|reduce)\b.{0,20}\b(tax|gst|vat|duty|tds)\b|fake (expense|invoice|bill|receipt|vendor)|inflate (the )?(invoice|expense|bill|claim)|round.?trip\w*)\b/.test(ut(a)),
      remediation: a => NO },
    { id: 'single_transaction_cap', group: 'Money & finance', basis: 'A single agent-initiated transfer has an absolute ceiling regardless of approvals in the chat.',
      get title() { return `No single agent-initiated money movement above ${inr(POLICY.singleTxnCap)}`; },
      test: a => a.cat === 'money' && a.value > POLICY.singleTxnCap,
      remediation: a => alt('Have a finance officer perform it directly, or split into approved tranches through the finance team.') },

    // ---- D. Data protection and credentials ----
    { id: 'no_credential_exposure', group: 'Data & credentials', basis: 'Secrets are never shown, sent or stored where others can read them.',
      title: 'Passwords, API keys, tokens, private keys, OTPs and card numbers are never revealed, sent, exported or posted',
      test: a => (/\b(share|send|email|mail|reveal|show|print|dump|export|post|paste|leak|expose|forward|publish|display|read out|tell me|give me|copy)\b.{0,30}\b(passwords?|passwd|api[ _-]?keys?|secret keys?|access tokens?|auth tokens?|bearer tokens?|private keys?|ssh keys?|otp|one.time (password|code)|cvv|cvc|card numbers?|pin codes?|credentials|recovery codes?|seed phrase)\b(?!\s*(polic|reset|rule|guideline|strength|requirement))/.test(ut(a))
        // a tool call whose own content carries a secret ("password: hunter2") is sending it out, whatever verb it uses
        || (a.source === 'tool' && /\b(password|passwd|api[ _-]?key|secret|access token|private key|otp|cvv)\b\s*(is|[:=])\s*\S{4,}/.test(ut(a))))
        && !/\b(reset|temporary|temp)\b.{0,80}\bsecure(ly)? (channel|link|portal|e-?mail|message)\b|\bvia (a )?secure (channel|link|portal)\b.{0,40}\b(reset|temporary|temp)\b/.test(ut(a)),
      remediation: a => alt('Use the secure reset or rotation flow, which never exposes the secret.') },
    { id: 'no_pii_external_share', group: 'Data & credentials', basis: 'Personal data may not leave the organisation for unapproved destinations (data-protection law).',
      title: 'Customer, employee or health data is not sent to personal email, public links or unapproved third parties',
      test: a => /\b(send|share|email|mail|upload|post|forward|sync|copy|move|publish|export|leak)\b.{0,50}\b(customer|client|user|patient|employee|staff|personal|pii|aadhaar|pan|medical|health|salary|payroll|financial) (data|records?|details|lists?|info\w*|files?|database)\b.{0,60}\b(personal (email|gmail|account|drive)|gmail|yahoo|hotmail|pastebin|public (link|url|bucket|drive|repo)|github|dropbox|whatsapp|telegram|discord|external|third.?party|outside (the )?(company|org\w*)|competitor|unapproved|home)\b/.test(ut(a)),
      remediation: a => alt('Share through the approved data-sharing process, with the data owner\'s sign-off, and mask identifiers first.') },
    { id: 'no_unmask_pii', group: 'Data & credentials', basis: 'Masked or anonymised data must not be re-identified.',
      title: 'Masked, redacted or anonymised personal data is not de-anonymised or re-identified',
      test: a => /\b(unmask|un.?redact|de.?anonymi[sz]\w*|re.?identif\w*|reveal (the )?(real|original) (names?|identit\w*)|link (the )?(anonymi[sz]ed|masked) (data|records?|ids?) (back )?to)\b/.test(ut(a)),
      remediation: a => alt('Ask the data owner to approve access to the original data through the normal access process.') },
    { id: 'no_covert_surveillance', group: 'Data & credentials', basis: 'Monitoring people without their knowledge breaches privacy and consent.',
      title: 'No covert tracking, monitoring or recording of a person (secretly, without their knowledge or consent)',
      test: a => /\b(track|monitor|spy on|stalk|follow|record|watch|tap|intercept|read (their|his|her))\b.{0,40}\b(employee|staff|person|partner|spouse|wife|husband|ex|girlfriend|boyfriend|neighbou?r|colleague|customer|someone|phone|messages|emails?|location|calls?)\b.{0,40}\b(secret\w*|covert\w*|hidden|undetected|without (them|him|her|their|his|anyone) (knowing|knowledge|consent|noticing)|without (their |his |her )?(knowledge|consent|permission)|behind (their|his|her) back)\b|\b(secret\w*|covert\w*|hidden)\b.{0,15}\b(track|monitor|record|spy|surveil\w*|spyware|keylogg\w*)\b/.test(ut(a)),
      remediation: a => alt('Use disclosed, consent-based monitoring that follows written policy.') },

    // ---- E. Harm, deception and fairness ----
    { id: 'no_malicious_code', group: 'Harm, deception & fairness', basis: 'Tools for attacking systems or people are never produced or run.',
      title: 'No malware, ransomware, phishing, exploits, DDoS or credential-harvesting content',
      test: a => /\b(write|create|build|make|generate|deploy|run|launch|send|craft|design|host)\b.{0,30}\b(malware|ransomware|keylogger|spyware|trojan|computer virus|worm|botnet|ddos|denial of service|exploit (code|kit)|phishing|credential harvest\w*|fake (login|sign.?in|bank) (page|site|portal)|spoof\w* (email|site|page)|sql injection payload|reverse shell)\b/.test(ut(a)),
      remediation: a => alt('For security testing, use an authorised, scoped engagement with a written agreement.') },
    { id: 'no_violence', group: 'Harm, deception & fairness', basis: 'Help with killing, hurting or abducting a person is never given, whatever the business context.',
      title: 'No plans, instructions or help to kill, injure, poison, abduct or threaten a person',
      test: a => {
        const t = ut(a);
        // a person as the object: pronouns, family and work relations, "people", or a named person after the verb
        const P = "(him|her|them|someone|somebody|anyone|people|persons?|humans?|child|children|kids?|babies|baby|women|woman|men|man|girls?|boys?|spouse|wife|husband|partner|girlfriend|boyfriend|ex|neighbou?rs?|boss|colleagues?|co-?workers?|classmates?|teachers?|mother|father|mom|dad|parents?|brother|sister|friends?|family|crush|roommates?|flatmates?|seniors?|juniors?|principal|professors?|lecturers?|landlord|relatives?|cousins?|uncle|aunt|grandmother|grandfather|siblings?|enemy|enemies|rival|the (ceo|manager|teacher|president|minister|officer|witness|judge|journalist)|[a-z]+'s (life|lives))";
        const obj = new RegExp('^(my |his |her |their |our |the |a |an )?(\\w+ )?' + P + '\\b');
        // verbs that are only about people when a person follows them (kill the process, shoot a goal, strangle options are not)
        const verb = /\b(kill|stab|shoot|poison|hurt|harm|injure|assault|torture|attack|threaten|strangl\w*|beat(?:ing)? up|execut\w*|terminat\w*|hang|drown|smother|suffocate|run over|burn)\w*\s+(.{0,40})/g;
        let m;
        while ((m = verb.exec(t))) {
          const rest = m[2];
          if (TECH_OBJ.test(rest) && !obj.test(rest)) continue;
          if (obj.test(rest) || new RegExp('^(\\w+ ){0,3}' + P + '\\b').test(rest.replace(/^(my|his|her|their|the|a|an) /, ''))) return true;
        }
        // names after verbs that always mean harming a person
        if (/\b(murder|assassinat\w*|kidnap\w*|abduct\w*|behead\w*|lynch\w*)\s+(?!of\b|mystery|novel|story|ballad|film|movie|case|trial|rate|weapon|scene|plot|scenes)\w+/.test(t) && !/\b(mystery|novel|fiction|screenplay|school play|crossword|board game)\b/.test(t)) return true;
        // "find people to shoot", "hire someone to hurt her"
        if (/\b(find|get|hire|recruit|looking for|need)\b.{0,30}\b(people|someone|somebody|men|guys|persons?)\b.{0,12}\b(to|for)\b.{0,6}\b(kill|shoot|stab|hurt|beat|attack|harm|murder|assault|rape)\w*/.test(t)) return true;
        // a capitalised personal name after the verb ("Kill John", "Murder Rahul Sharma"), unless it is a product or tool name
        const raw = String(a.raw || '');
        const nm = /\b(?:[Kk]ill(?:ing)?|[Mm]urder|[Ss]hoot|[Ss]tab|[Ss]trangle|[Pp]oison|[Aa]ssassinate)\s+(?:(?:mr|mrs|ms|dr)\.?\s+)?([A-Z][a-z]{2,})/.exec(raw);
        if (nm && !/^(Python|Java|Node|Chrome|Docker|Linux|Windows|Mac|Excel|Word|Slack|Teams|Zoom|Jira|Redis|Postgres|Mysql|Apache|Nginx|Kubernetes|Process|Job|Task|Server|Session|App|Port|Bug|Switch|Script|Service|Cron|Thread|Query|Batch|Photo|Video|Scene|Meeting|Account|Contract|Subscription|Ticket|Order|Lights?|The|This|That|All|Every|Any|Your|Our|Time|Mode)$/.test(nm[1])) return true;
        // "kill customer 7": a numbered customer, client, employee ... is a person, not a process
        if (/\b(?:kill|murder|shoot|stab|poison)\w*\s+(?:customer|client|employee|vendor|patient|member|borrower|supplier|manager|user)\s*(?:#|no\.?|number|id)?\s*\d+(?!\S*\s*(?:'s\s+)?(?:session|process|job|connection|task|thread))/.test(t)) return true;
        // an attack on a place full of people: "plan to blast my college", "how to blow up the school"
        if (/\b(blast|blow(?:ing)? up|bomb(?:ing)?|nuke|torch|burn(?:ing)? down|shoot(?:ing)? up|massacre|set(?:ting)? fire to)\s+(?:\w+\s+){0,2}(?:college|school|university|campus|classroom|office|building|mall|temple|church|mosque|synagogue|hospital|station|airport|stadium|market|crowd)\b/.test(t) && !/\b(email|e-mail|message|sms|notification|campaign|marketing|promo)\b/.test(t)) return true;
        return false;
      },
      remediation: a => alt('If someone is in danger, contact the emergency services. I cannot help plan harm to a person.') },
    { id: 'no_harmful_content', group: 'Harm, deception & fairness', basis: 'Content that demeans a group, supports crime or abuse, or harasses a person is not produced, whatever the system the request arrives through.',
      title: 'No hateful, harassing, criminal-assistance or abusive content (recognised by the policy model)',
      test: a => false,      // no fixed pattern: this rule exists through the policy model, trained on real harmful requests
      remediation: a => alt('Rephrase the request without targeting a person or group, or ask for a neutral, factual version.') },
    { id: 'no_discrimination', group: 'Harm, deception & fairness', basis: 'Decisions may not rest on protected characteristics.',
      title: 'No decision (hiring, lending, pricing, access) based on caste, religion, gender, race, ethnicity, age, disability, pregnancy, marital status, sexuality or nationality',
      test: a => /\b(reject|deny|decline|exclude|filter out|screen out|block|refuse|blacklist|downgrade|charge more|pay less|don'?t (hire|lend|approve|serve)|never (hire|lend|approve|serve))\b.{0,60}\b(because of|based on|due to|on the basis of|on account of|if (they|he|she) (is|are|was) an?|who are|from the)\b.{0,15}\b(caste|religion|muslims?|hindus?|christians?|sikhs?|jews?|dalits?|gender|women|woman|men|female|male|race|racial|ethnic\w*|black|african|disabled|disabilit\w*|pregnan\w*|married|unmarried|single mothers?|gay|lesbian|transgender|lgbt\w*|nationality|foreigners?|immigrants?|age)\b|\b(only|just|prefer(ably)?) (hire|accept|approve|serve|lend to)\b.{0,10}\b(men|women|males|females|hindus?|muslims?|christians?|young|under \d+|upper caste|locals)\b/.test(ut(a)),
      remediation: a => alt('Base the decision on job-related or credit-related criteria and record them.') },
    { id: 'no_fake_content', group: 'Harm, deception & fairness', basis: 'Fabricated reviews and testimonials mislead the public (consumer-protection law).',
      title: 'No fake reviews, testimonials, ratings, endorsements or false advertising claims',
      test: a => /\b(fake|fabricat\w*|bogus|false|made.?up|fictitious|paid|astroturf\w*|bulk)\b.{0,25}\b(reviews?|testimonials?|ratings?|endorsements?|star ratings?|upvotes?|followers?|likes?)\b|\b(write|post|generate|create)\b.{0,15}\b(\d+ |many |several |multiple )?(5.star |positive |glowing )?(reviews?|testimonials?)\b.{0,25}\b(pretend\w*|as if|posing|from (different|fake|multiple) (customers|users|accounts))\b|\bmislead\w*\b.{0,15}\b(customers?|users?|public|investors?|regulators?)\b|\bfalse (claim|advertis\w*|statement)s?\b/.test(ut(a)),
      remediation: a => alt('Ask real customers for reviews and publish them as they are.') },
    { id: 'no_spam', group: 'Harm, deception & fairness', basis: 'Unsolicited bulk messaging breaks anti-spam and consent rules.',
      title: 'No spam or bulk messages to purchased, scraped or non-consenting lists',
      test: a => /\b(spam|unsolicited|cold blast|mass mail)\b|\b(purchased|bought|scraped|harvested|rented|scrap\w*)\b.{0,15}\b(email|mailing|contact|phone|number)s?\b.{0,12}\b(lists?|addresses|database)?\b|\b(email|message|sms|text|whatsapp)\b.{0,40}\bwithout (their |the )?(consent|permission|opt.?in)\b|\bignore (the )?(unsubscribe|opt.?out|do not (call|email|disturb)|dnd)\b/.test(ut(a)) && (a.type === 'send_bulk_email' || /\b(send|blast|email|mail|message|sms|whatsapp|broadcast|campaign)\b/.test(ut(a))),
      remediation: a => alt('Send only to a list with recorded opt-in and a working unsubscribe link.') },

    // ---- E2. Rules that need a lookup list (policy_data/) ----
    { id: 'listed_sanctioned_party', group: 'Harm, deception & fairness', basis: 'Dealing with a party on the sanctions or blacklist register is prohibited.',
      title: 'No payment, transfer or onboarding of a party that is on the sanctions / blacklist register',
      test: a => !!(a.policyCheck && a.policyCheck.ok && a.policyCheck.lookups.sanctions && a.policyCheck.lookups.sanctions.status === 'hit') && /\b(pay|payment|transfer|wire|send|remit|settle|release|disburse|onboard|add|approve|refund)\b/.test(ut(a)),
      remediation: a => alt('Stop and refer to compliance; do not proceed without a documented clearance.') },
    { id: 'unconsented_recipient_list', group: 'Harm, deception & fairness', basis: 'Messages may only go to people who agreed to receive them.',
      title: 'No message to a recipient list that the consent register marks as "no consent"',
      test: a => !!(a.policyCheck && a.policyCheck.ok && a.policyCheck.lookups.consent && a.policyCheck.lookups.consent.status === 'hit') && /\b(send|email|mail|message|sms|text|whatsapp|blast|broadcast|campaign|notify|push)\b/.test(ut(a)),
      remediation: a => alt('Send only to a list with recorded opt-in and a working unsubscribe link.') },

    // ---- F. Irreversible operational damage ----
    { id: 'no_backup_destruction', group: 'Irreversible damage', basis: 'Backups and snapshots are the way back from any mistake; removing them removes reversibility itself.',
      title: 'Backups, snapshots and recovery points are never deleted, disabled or overwritten by an agent',
      test: a => /\b(delete|erase|wipe|destroy|remove|purge|disable|turn off|stop|drop|corrupt|overwrite|truncate)\b.{0,25}\b(all |the |every |old |our )?(backups?|snapshots?|restore points?|recovery points?|disaster recovery|replicas?|point.in.time recovery|pitr|versioning)\b/.test(ut(a)) && !/\b(older than|expired|per (the )?retention|rotation|rotate)\b/.test(ut(a)),
      remediation: a => alt('Ask the platform owner; expired backups are removed by the retention schedule, not by an agent.') },
    { id: 'no_prod_destruction', group: 'Irreversible damage', basis: 'Dropping production data or systems is catastrophic and cannot be undone by the guardrail.',
      title: 'No drop, truncate, wipe or format of production databases, servers, storage or the whole system',
      test: a => /\b(drop|truncate|wipe|format|destroy|nuke|erase|rm -rf|delete)\b.{0,25}\b(the |all |entire |whole |every )?(production|prod|live|primary|main)\b.{0,15}\b(databases?|db|servers?|clusters?|buckets?|volumes?|disks?|tables?|data|environment|systems?|storage)\b|\b(drop|truncate)\b.{0,10}\b(database|schema|all tables)\b|\brm -rf\s+\/|\bformat (the )?(disk|drive|server)\b|\b(delete|wipe|erase) (the )?(entire|whole) (database|system|server|company|bucket|drive|disk)\b/.test(ut(a)),
      remediation: a => alt('Perform maintenance on a copy first, with a verified snapshot and a person executing the change.') },
  ];
  const SOFT_RULES = [
    { id: 'bulk_email_consent', title: 'Bulk email requires a consent-verified recipient list',
      test: a => a.type === 'send_bulk_email', pts: 6 },
    { id: 'high_value_money', get title() { return `Money movement above ${inr(POLICY.dualApproval)} needs dual approval`; },
      test: a => a.cat === 'money' && a.value > POLICY.dualApproval, pts: 5 },
    { id: 'wildguard_alone', title: 'WildGuard flagged this and nothing else agrees (adds a little risk, does not decide)',
      test: a => POLICY.wgUncorroborated === 2 && !!(a.wg && a.wg.ok && a.wg.harmful_request) && !a.injection && !a.harmful && !(a.policyCheck && a.policyCheck.ok && a.policyCheck.model && a.policyCheck.model.rule !== 'none' && a.policyCheck.model.p >= POLICY.modelReview), pts: 3 },
    { id: 'lookup_unavailable', title: 'A payment to a named party could not be checked against the sanctions list (list missing or empty)',
      test: a => (a.type === 'transfer_funds' || a.type === 'approve_payment') && !!(a.policyCheck && a.policyCheck.ok && a.policyCheck.lookups.sanctions && a.policyCheck.lookups.sanctions.status === 'unavailable'), pts: 3 },
    { id: 'pii_export', title: 'Exports containing customer data need a data-owner sign-off',
      test: a => a.type === 'export_data' || /(export|download|dump).{0,30}(customer|user|pii|emails)/.test(a.text), pts: 5 },
    { id: 'retention_cleanup', title: 'Deleting data inside the retention window is restricted',
      test: a => a.type === 'bulk_delete' && !/(older than|retention|stale|expired)/.test(a.text), pts: 4 },
  ];

  const DEFAULT_CFG = {
    w: { R: 0.5, P: 0.25, I: 0.25 },
    t1: 2.0, t2: 5.0,                       // LOW/MID and MID/HIGH cut points on the 0-10 score
    traj: { wA: 0.25, wB: 0.30, wC: 0.25, wD: 0.20, tau1: 0.35, tau2: 0.65,
            budget: 30, lambda: 0.7, window: 5, structWindow: 10, structThreshold: 900 },
  };

  /* ---------- helpers ---------- */
  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const r1 = x => Math.round(x * 10) / 10;
  const num = s => parseFloat(String(s).replace(/,/g, ''));

  /* ---------- typo tolerance ----------
     Misspellings must not be a way round the rules ("delte", "appiintment", "recored"). Words are matched
     to a vocabulary of action and document terms by edit distance (a swap or one slip for short words, two
     for long ones, same first letter). The corrected text is only ever ADDED to what the rules see, and the
     action type is re-read from it only when the original wording was not understood. */
  const VOCAB = ['delete','remove','erase','destroy','purge','wipe','drop','clear','records','record','company','customer','customers','account','accounts','database','subscription','payment','payments','invoice','invoices','refund','transfer','approve','cancel','update','change','modify','create','generate','prepare','schedule','export','download','send','email','emails','campaign','appointment','offer','experience','relieving','joining','employment','promotion','letter','letters','certificate','certificates','marksheet','degree','diploma','payslip','salary','package','fake','forged','forge','fraud','fabricate','counterfeit','duplicate','signature','letterhead','instructions','previous','ignore','password','credentials','aadhaar','passport','everything','archived','permanently'];
  const TYPO_STOP = new Set(['remote','remold','promote','sender','render','sendme','deletes','records.','changed','changes','changer','modified','created','updated','removed','sent','drops','clean','cleared','creator','scheduler','exports','exporter','download','downloads','accounts']);
  function osa(a, b) {
    const d = Array.from({ length: a.length + 1 }, (_, i) => [i].concat(Array(b.length).fill(0)));
    for (let j = 1; j <= b.length; j++) d[0][j] = j;
    for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) {
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
    return d[a.length][b.length];
  }

  /* ---------- text normalisation: seeing through obfuscation ----------
     Someone who knows the rules are wording-based can dodge them with look-alike letters, spaced-out words, leetspeak,
     encoded text or another language. normalizeText() returns EXTRA readings of the request (never replacing the
     original): each one is appended to the text the rules and models read, so a hidden "drop the production database"
     is seen as well as the disguise. It reads text only; it cannot judge a request whose meaning is hidden by context. */
  const ZERO_WIDTH = /[­؜᠎​-‏‪-‮⁠-⁤﻿]/g;
  const CONFUSABLES = { 'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'у': 'y', 'х': 'x', 'і': 'i', 'ј': 'j', 'ѕ': 's', 'ԁ': 'd', 'ɡ': 'g', 'ᴅ': 'd', 'ο': 'o', 'α': 'a', 'ν': 'v', 'τ': 't', 'ρ': 'p', 'ε': 'e', 'ι': 'i', 'κ': 'k', 'η': 'n', 'μ': 'u', 'ⅼ': 'l', 'Ι': 'i', 'Ο': 'o', 'Α': 'a', 'Β': 'b', 'Ε': 'e', 'Ρ': 'p', 'Τ': 't', 'Χ': 'x', 'А': 'a', 'В': 'b', 'С': 'c', 'Е': 'e', 'Н': 'h', 'К': 'k', 'М': 'm', 'О': 'o', 'Р': 'p', 'Т': 't', 'Х': 'x' };
  const LEET = { '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't', '8': 'b', '@': 'a', '$': 's', '!': 'i' };
  const KEYWORDS = /(drop|delete|erase|wipe|password|ignore|transfer|backup|database|production|bypass|override|disable|launder|steal|fraud|phish|malware|ransom|guardrail|admin|credential)/;
  const HINGLISH = [  // romanised Hindi / Devanagari -> English keyword (a small lexicon, not translation)
    [/\b(mita ?do|mitao|mita ?de|hata ?do|hatao|hata ?de|khatam ?kar|uda ?do|udao|saaf ?kar)\b/g, 'delete'], [/\b(bhej ?do|bhejo|bhej ?de|bhej)\b/g, 'send'], [/\b(transfer ?kar|paisa ?bhej)\b/g, 'transfer'],
    [/\b(sabhi|saare|saari|sab|poora|puri)\b/g, 'all'], [/\b(paisa|paise|rupaye|dhan)\b/g, 'money'], [/\b(nakli|naqli|jaali|jali|fake)\b/g, 'fake'], [/\b(chori|chura)\b/g, 'steal'],
    [/\b(dhokha|dhokhadhadi)\b/g, 'fraud'], [/\b(band ?kar|band ?karo|rok ?do)\b/g, 'turn off'], [/\b(password|pasvard)\s*(batao|dikhao|do|bhejo)\b/g, 'reveal password'],
    [/\b(batao|dikhao)\b/g, 'show'], [/\b(naya|nayi)\b/g, 'new'], [/\bbina\b/g, 'without'], [/\b(manzoori|anumati)\b/g, 'approval'],
    [/हटा(ओ|दो|ना)?/g, ' delete '], [/मिटा(ओ|दो|ना)?/g, ' delete '], [/भेज(ो|ना|दो)?/g, ' send '], [/पासवर्ड/g, ' password '], [/बैकअप/g, ' backup '], [/डेटाबेस/g, ' database '],
    [/(सारे|सभी|सब)/g, ' all '], [/(नकली|जाली)/g, ' fake '], [/पैसे?/g, ' money '], [/ट्रांसफ़?र/g, ' transfer '], [/रिव्यू/g, ' reviews '], [/(लिखो|लिख दो|बनाओ)/g, ' write '], [/बंद/g, ' turn off '], [/(लॉग|लोग)/g, ' log '], [/चोरी/g, ' steal '], [/प्रोडक्शन/g, ' production '], [/ऑडिट/g, ' audit '],
  ];
  const rot13 = t => t.replace(/[a-z]/gi, c => String.fromCharCode((c <= 'Z' ? 90 : 122) >= (c = c.charCodeAt(0) + 13) ? c : c - 26));
  const COMMON = new Set('the and for you that with this have from your not but are all can will one our out use how what when who which their there about would could should please just into over only also than then them these those more some such any each been being was were has had did does done get got make made take send show give tell let need want like know see look find think help call keep put read write run set add open close pay new old new now him her his its they she may must say said does own after before again against between through during above below until while because since without within under every other another many most much very still well back even here too off down both few same so is it to of in on at as by an or if be do we me my no up us am he'.split(' '));
  function lexScore(str) {
    const toks = (String(str).match(/[a-z]{2,}/g) || []);
    if (toks.length < 2) return 0;
    let hit = 0; toks.forEach(w => { if (dictHas(w)) hit++; });
    return hit / toks.length;
  }
  function printableText(bytes) {
    let ok = 0; for (const b of bytes) if ((b >= 32 && b < 127) || b === 10 || b === 9) ok++;
    return bytes.length >= 6 && ok / bytes.length >= 0.9 ? String.fromCharCode.apply(null, bytes) : null;
  }

  const RULE_WORDS = ('drop truncate wipe format destroy nuke erase delete remove purge production prod live database databases table tables server servers backup backups snapshot snapshots restore recovery audit logs log logging ledger evidence '
    + 'guardrail guardrails policy policies safety checks threshold risk engine approval review signoff bypass override disable skip ignore circumvent admin root privileges access permissions autonomy password passwords key keys token tokens credentials secret otp '
    + 'transfer payment pay refund wire send email mail share upload post export customer customers employee employees patient personal data records list gmail account accounts public private external third party '
    + 'fake forged forge reviews review ratings testimonials phishing malware ransomware keylogger spyware botnet steal fraud launder laundering kyc aml sanctioned sanctions backdate invoice invoices contract tax gst cash books '
    + 'impersonate pretend ceo bank police official government monitor track spy secretly without consent spam bulk campaign unmask reidentify anonymised masked redacted hire reject loan applicants religion caste gender').split(' ');
  const DICT = new Set(); RULE_WORDS.forEach(w => DICT.add(w));
  function dictHas(w) { return DICT.has(w) || COMMON.has(w) || VOCAB.includes(w); }
  // "droptheproductiondatabase" -> "drop the production database" (minimum-cost split; unknown letters are allowed at a price)
  function segmentWords(str) {
    const n = str.length, best = new Array(n + 1).fill(Infinity), prev = new Array(n + 1).fill(0);
    best[0] = 0;
    for (let i = 1; i <= n; i++) {
      for (let j = Math.max(0, i - 18); j < i; j++) {
        const w = str.slice(j, i);
        const c = dictHas(w) ? (w.length === 1 ? 1.4 : 1) : (i - j === 1 ? 3 : Infinity);
        if (best[j] + c < best[i]) { best[i] = best[j] + c; prev[i] = j; }
      }
    }
    const out = []; for (let i = n; i > 0; i = prev[i]) out.unshift(str.slice(prev[i], i));
    const unknown = out.filter(w => w.length === 1 && !dictHas(w)).length;
    return unknown <= Math.floor(n / 6) ? out.join(' ') : null;
  }
  const NORMALIZE = { on: true };      // switch only for measuring what the normaliser adds (tests/run_adversarial.js)
  function normalizeText(raw) {
    if (!NORMALIZE.on) return [];
    const out = [], base = String(raw || '').toLowerCase().slice(0, 6000);
    const add = v => { v = String(v || '').replace(/\s+/g, ' ').trim(); if (v && v !== base && !out.includes(v)) out.push(v); };
    let t = base;
    try { t = t.normalize('NFKC'); } catch (e) { /* old engines */ }
    t = t.replace(ZERO_WIDTH, '');
    try { t = t.normalize('NFD').replace(/[̀-ͯ]/g, ''); } catch (e) { /* ignore */ }
    t = t.replace(/[^\u0000-\u007f]/g, c => CONFUSABLES[c] || CONFUSABLES[c.toUpperCase()] || c);
    add(t);
    // spaced or punctuated letters: "d r o p", "d.r.o.p", and a whole sentence spaced letter by letter
    const joined = t.replace(/(?:^|(?<=[^a-z]))(?:[a-z][\s.\-_*]{1,}){3,}[a-z](?=$|[^a-z])/g, run => {
      if (/\s{2,}/.test(run)) return run.split(/\s{2,}/).map(w => w.replace(/[\s.\-_*]+/g, '')).join(' ');   // double spaces mark the word breaks
      const letters = run.replace(/[\s.\-_*]+/g, '');
      return segmentWords(letters) || letters;
    });
    add(joined);
    // leetspeak inside words: "dr0p the pr0duct1on databa5e"
    const leet = (str, one) => str.replace(/[a-z0-9@$!]+/g, w => /[a-z]/.test(w) && /[0-9@$!]/.test(w) && !/^\d+$/.test(w) && !/^\d{2,}[a-z]{0,2}$/.test(w) ? w.replace(/[0-9@$!]/g, c => (c === '1' ? one : LEET[c])) : w);
    add(leet(joined, 'i')); add(leet(joined, 'l'));
    // encoded: URL escapes, HTML entities, base64, hex, rot13, reversed
    try { if (/%[0-9a-f]{2}/.test(t)) add(decodeURIComponent(t.replace(/\+/g, ' '))); } catch (e) { /* not URL-encoded */ }
    if (/&#?\w+;/.test(t)) add(t.replace(/&#x([0-9a-f]+);/gi, (m, h) => String.fromCharCode(parseInt(h, 16))).replace(/&#(\d+);/g, (m, d) => String.fromCharCode(+d)).replace(/&amp;/g, '&'));
    (String(raw || '').match(/[A-Za-z0-9+/_-]{16,}={0,2}/g) || []).forEach(tok => {
      try { const bin = (typeof atob === 'function' ? atob : null); if (bin) { const d = bin(tok.replace(/-/g, '+').replace(/_/g, '/')); const txt = printableText(Array.from(d, c => c.charCodeAt(0))); if (txt) add(txt.toLowerCase()); } } catch (e) { /* not base64 */ }
    });
    (t.match(/\b(?:[0-9a-f]{2}){8,}\b/g) || []).forEach(tok => { const bytes = tok.match(/../g).map(h => parseInt(h, 16)); const txt = printableText(bytes); if (txt) add(txt.toLowerCase()); });
    // text that only becomes English after a simple scramble (ROT13, reversed, each word reversed). Checked on the whole
    // text and on each part after a colon or full stop, because the disguise is usually preceded by a plain sentence.
    const parts = [t].concat(t.split(/[:.!?\n]+/).map(x => x.trim()).filter(x => x.length >= 12));
    parts.forEach(seg => {
      const here = lexScore(seg);
      [rot13(seg), seg.split('').reverse().join(''), seg.split(/\s+/).map(w => w.split('').reverse().join('')).join(' ')].forEach(v => { if (lexScore(v) >= 0.4 && lexScore(v) >= here + 0.25) add(v); });
    });
    // another language: swap known Hindi / Hinglish words for English ones
    let h = joined, changed = false;
    HINGLISH.forEach(([re, en]) => { const n = h.replace(re, ' ' + en + ' '); if (n !== h) { h = n; changed = true; } });
    if (changed) add(h);
    // Hindi puts the verb last ("production database delete do"); English rules expect it first
    [t, h].forEach(str => {
      const m = str.replace(/\s+/g, ' ').trim().match(/^(.*?)\s*\b(delete|send|transfer|steal|turn off|show|write|reveal password)\b(?:\s+(?:do|kar|karo|de))*\s*$/);
      if (m && m[1] && /\b(do|kar|karo|de|ka|ki|ke|ko|par|meri|mera)\b|[^\u0000-\u007f]|\b(delete|send|transfer|steal|show|write)\s+(do|kar|karo|de)\s*$/.test(str)) add(m[2] + ' ' + m[1]);
    });
    return out.join(' ').length > 4000 ? out.slice(0, 4) : out;
  }

  function fixTypos(s) {
    return String(s).replace(/[a-z]{5,}/g, w => {
      if (VOCAB.includes(w) || TYPO_STOP.has(w)) return w;
      const max = w.length >= 8 ? 2 : 1;
      let best = null, bd = 99;
      for (const v of VOCAB) {
        if (v[0] !== w[0] || Math.abs(v.length - w.length) > max) continue;
        const dist = osa(w, v); if (dist <= max && dist < bd) { best = v; bd = dist; }
      }
      return best || w;
    });
  }

  /* ---------- natural language -> structured action ---------- */
  function fromPrompt(text, ctx) {
    const a = fromPromptRaw(text, ctx);
    if (a.type === 'unknown_action') {
      const lower = text.trim().toLowerCase(), fixed = fixTypos(lower);
      if (fixed !== lower) { const b = fromPromptRaw(text, ctx, fixed); if (b.type !== 'unknown_action') { b.typoFixed = true; return b; } }
      for (const v of normalizeText(text)) { const b = fromPromptRaw(text, ctx, fixTypos(v)); if (b.type !== 'unknown_action') { b.normalized = true; return b; } }
      // Still nothing: if it names no operation, no kind of record and no number, it is conversation (a follow-up, a format request,
      // a remark), not an action. The hard rules, policy model and safety model still read it; this only stops harmless chat being held.
      const low = text.toLowerCase();
      // "my Aadhaar number is 123456789012": the person only states their own sensitive data, with no operation and no lookup wording.
      // It is flagged on the page as sensitive data and is not held for a person just because it carries a number.
      if (detectPII(text).count > 0 && !/\b(disable|shut ?down|terminate|kill|reset|revoke|block|ban|suspend|deactivate|override|bypass|hack|steal|launch|deploy|publish|post|buy|sell|trade|withdraw|deposit|invest|install|reboot|restart|lock|unlock|encrypt|decrypt|upload|share|submit|apply|execute|run|trigger|disclose|leak|reveal|expose|enable|switch|migrate|push|pay|charge|debit|invoice|bill|delete|remove|transfer|send|update|edit|export|download|fetch|get|find|show|give|pull|look ?up|retrieve|extract|search|dump|list|display|tell me|details|information|info|profile|history|related to|linked to|associated with)\b/.test(low)) {
        const b = fromPromptRaw(text, ctx, 'what is ' + low); b.conversational = true; return b;
      }
      if (!new RegExp('\\b(disable|shut ?down|terminate|kill|reset|revoke|block|ban|suspend|deactivate|override|bypass|hack|steal|impersonat\w*|launch|deploy|publish|post|buy|sell|trade|withdraw|deposit|invest|hire|fire|install|uninstall|reboot|restart|stop|halt|lock|unlock|encrypt|decrypt|upload|share|submit|file|apply|order|book|reserve|execute|run|invoke|trigger|disclose|leak|reveal|expose|enable|turn (on|off)|switch|migrate|merge|commit|push|pay|charge|debit|credit|invoice|bill|\d{4,})\\b|\\b(customers?|users?|accounts?|records?|orders?|payments?|funds?|money|wallets?|database|server|production|data|files?|logs?|invoices?|rows?|tables?|admin|password|credentials?)\\b|\\d').test(low)) {
        const b = fromPromptRaw(text, ctx, 'what is ' + low); b.conversational = true; return b;
      }
    }
    return a;
  }
  function fromPromptRaw(text, ctx, forced) {
    const t = text.trim();
    const l = forced || t.toLowerCase();
    let type = null, target = 'record', table = null, conf = 0.97;

    const money = l.match(/(?:rs\.?|₹|inr|\$)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:rupees|inr|dollars)/);
    let value = money ? num(money[1] || money[2]) : 0;
    // "2 crore", "9 lakh", "1.5 million": the unit multiplies the number. A bare large number next to a money verb counts too.
    const unit = l.match(/([\d,]+(?:\.\d+)?)\s*(crores?|cr|lakhs?|lacs?|million|mn|k)\b/);
    if (unit) { const mult = /^(crore|cr)/.test(unit[2]) ? 1e7 : /^(lakh|lac)/.test(unit[2]) ? 1e5 : /^(million|mn)/.test(unit[2]) ? 1e6 : 1e3; value = Math.max(value, num(unit[1]) * mult); }
    else if (!value && /\b(transfer|wire|remit|pay(?:ment)?|send|refund|approve|release|disburse)\b/.test(l)) { const big = l.match(/(?<![\w.-])(\d[\d,]{4,})(?![\w-])/); if (big && num(big[1]) >= 10000) value = num(big[1]); }
    const cnt = l.match(/(\d[\d,]*)\s*(?:[a-z]+\s+){0,2}?(?:records|rows|logs|entries|recipients|customers|users|people|emails|files|linked|dependents)/);
    let count = cnt ? num(cnt[1]) : null;

    // A question ("what is our refund policy?") is read-only even when it mentions an action word,
    // unless a command follows it ("...? also delete all users").
    const ACT = '(delete|remove|drop|erase|destroy|purge|wipe|send|transfer|refund|cancel|approve|pay|update|change|edit|modify|create|add|schedule|export|sync)';
    const chained = new RegExp('([.;!?]|\\band\\b|\\bthen\\b|\\balso\\b)\\s*(please\\s+)?' + ACT + '\\b').test(l);
    const question = /^(what|whats|what's|how|who|when|where|why|which|explain|tell me|define|can you tell|give me|show me|get me|list|find|look up|fetch|display|view|summari[sz]e|print|search|count the|(generate|prepare|build|produce|compile|create) (a |an |the )?(\w+ )?(report|summary|overview|dashboard|list)\b|check (the |my |our |on |if |whether |how |what |current )|hi|hello|hey|thanks|thank you)\b/.test(l);
    // Telling the assistant who you are, or how to word a reply, is context and not an operation (only when no action word appears at all).
    const context = !new RegExp('\\b' + ACT + '\\b|\\b(grant|revoke|reset|disable|enable|open|close|block|unblock|run|execute|share|publish|post|buy|purchase|withdraw|deposit|invest|hire|fire)\\b').test(l) &&
      (/^(my name is|i am|i'm|i’m|this is|call me|you can call me|my (role|team|department|company|office) is|i work (in|at|for|as)|sign (it|this|that|the \w+) as|use the name|write it from|make it (shorter|longer|formal|polite|casual|friendlier))\b/.test(l) || /^(also |and |please |then )?(mention|include|say|keep|shorten|reword|rephrase|rewrite|expand|elaborate|use a|make (it|the \w+) (more|less)|mention that)\b/.test(l) || /^[\w ,.'’-]{1,40} here$/.test(l) ||
       /\b(in (the )?form(at)? of|as (a |an )?|into (a |an )?|to (a |an )?)(pdf|word|docx|text file|html|document)\b/.test(l) || /\b(make|turn|save|get|give|provide|convert|download)\b.{0,40}\b(pdf|docx)\b/.test(l));
    if (/^[\d\s+\-*/().=x×÷^%,]+$/.test(l) || (question && !chained) || context) {
      type = 'read_data'; target = 'general_query'; conf = 0.95;
    } else if (/(aadhaar|aadhar|pan card|passport|driving licen[cs]e|voter id|government id|gov id)/.test(l) &&
        /(edit|change|alter|modify|update|retouch|replace|crop)/.test(l)) {
      type = 'edit_document'; target = 'government_id_photo';
    } else if (/(profile (picture|photo|pic)|avatar)/.test(l) && /(edit|change|update|replace)/.test(l)) {
      type = 'edit_document'; target = 'profile_photo';
    } else if (/(ignore|disregard|forget).{0,20}(previous|prior|above|earlier).{0,20}(instruction|rule|prompt)/.test(l)) {
      type = /(transfer|send|pay)/.test(l) ? 'transfer_funds' : 'update_field'; target = 'customer_record';
    } else if (/\b(grant|revoke|assign|give|remove|change|set|update|modify|elevate|promote|demote|upgrade)\b.{0,30}\b(admin(istrator|istrative)?|roles?|permissions?|access (rights|levels?|permissions?)|privileges?|read.?write|write (access|rights)|full access|rights)\b/.test(l) && !/\b(password|display name|profile)\b/.test(l)) {
      type = 'change_permissions'; target = 'user_permissions';
    } else if (/(delet|remov|purg|wipe|clear|eras|drop|destroy)\w*.{0,30}\b(all|every|everything|entire|whole)\b/.test(l) || /(bulk|mass|all|every|purge|wipe|clear).{0,25}(delete|remove|records?|logs|rows)|(delete|remove|purge|wipe|clear).{0,25}(\d[\d,]*)\s*(records|rows|logs|entries|files)/.test(l) && /(delet|remov|purg|wipe|clear|drop)/.test(l)) {
      type = 'bulk_delete'; target = 'archived_logs';
    } else if (/(delete|drop|remove|erase|destroy)/.test(l)) {
      type = 'delete_record';
    } else if (/refund/.test(l)) {
      type = 'process_refund'; target = 'payment_gateway';
    } else if (/\b(draft|compose|write|prepare|proofread|rewrite)\b.{0,25}\b(e-?mails?|mails?|messages?|repl(y|ies))\b/.test(l) && !/\b(send|blast|push|launch|dispatch)\b/.test(l)) {
      type = 'create_record'; target = 'email_draft';
    } else if (/\b(newsletter|campaign|mailer|sms blast|broadcast|blast|mass (e-?mail|mail|message|text)|mailing lists?|(e-?mail|contact|phone|distribution|recipient)s? lists?|subscribers|recipients|everyone|all (the )?(customers|users|clients|members|subscribers|employees|staff|contacts))\b/.test(l) && /\b(send|blast|push|launch|dispatch|broadcast|e-?mail|mail|text|message|notify)\b/.test(l) && !/\b(schedule|book|arrange|set up)\b.{0,20}\b(meeting|call|appointment|calendar|sync)\b/.test(l)) {
      type = 'send_bulk_email'; target = 'mailing_list';
    } else if (/\b(send|forward|e-?mail|mail|reply to)\b.{0,40}\b(e-?mails?|mail|messages?|notes?|confirmations?|repl(y|ies)|invoices?|receipts?)\b|\be-?mail (the|a|an|my|our)?\s*\w+/.test(l) && /\b(send|forward|e-?mail|mail|reply)\b/.test(l)) {
      type = 'send_email'; target = 'single_email';
    } else if (/(transfer|wire|send money|pay out|payout|move funds|remit)/.test(l)) {
      type = 'transfer_funds'; target = 'wallet';
    } else if (/(approve|release|authori[sz]e).{0,25}(payment|invoice|payout|purchase order|\bpo\b)|pay (the )?vendor/.test(l)) {
      type = 'approve_payment'; target = 'vendor_payment';
    } else if (/cancel.{0,20}subscription|subscription.{0,20}cancel/.test(l)) {
      type = 'cancel_subscription'; target = 'subscription_record'; table = 'subscriptions';
    } else if (/(export|download|dump|extract).{0,30}(data|customers|users|records|emails|database)/.test(l)) {
      type = 'export_data'; target = 'customer_data';
    } else if (/(schedule|calendar|meeting|appointment)/.test(l) && !/lett\w*|certificat\w*/.test(l)) {
      type = 'create_calendar_event'; target = 'user_calendar';
    } else if (/(sync|integrat|webhook|connect to|third.party)/.test(l)) {
      type = 'sync_external_integration'; target = 'third_party_service'; conf = 0.55;
    } else if (/(update|change|edit|set|correct|fix|rename|modify)/.test(l)) {
      type = 'update_field'; target = 'customer_record';
    } else if (/(create|add|open|log|register|new |make|generate|write|draft|prepare|compose)/.test(l)) {
      type = 'create_record'; target = 'support_ticket';
    } else if (/(preview|summari[sz]e|report|read|show|list|view|fetch|look up|generate)/.test(l)) {
      type = 'generate_report_preview'; target = 'internal_dashboard'; conf = 0.93;
    } else {
      type = 'unknown_action'; conf = 0.7;   // unclear request: a human takes a look
    }

    if (!table) {
      if (/(\busers?\b|customer|account)/.test(l) && /(delete|remove|drop|erase)/.test(l)) table = 'users';
      else if (/(product|listing|catalog|sku)/.test(l)) { table = 'products'; if (type === 'delete_record') target = 'product_listing'; }
      else if (/\borders?\b/.test(l)) table = 'orders';
      else if (/invoice/.test(l) && type === 'delete_record') table = 'invoices';
    }

    // "delete all …" with no number: the scope is everything, so treat it as very large
    if (type === 'bulk_delete' && count == null && /\b(all|every|everything|entire|whole)\b/.test(l)) count = 1000;
    const noBackup = /(no (backup|snapshot)|without (a )?(backup|snapshot)|not backed up|can.?t be undone|cannot be undone|permanent(ly)?|irreversibl)/.test(l);
    const hasBackup = /(with (a )?(backup|snapshot)|backed up|backup exists|snapshot (exists|taken))/.test(l);
    const snapshot = noBackup ? false : hasBackup ? true : ctx.snapshot;
    const irreversible = /(already (been )?(sent|processed|charged|dispatched|paid|delivered|transferred|settled)|cannot be (recalled|recovered)|irreversible)/.test(l) ||
                         (type === 'send_bulk_email') ||
                         (type === 'process_refund' && /gateway|already/.test(l));

    const tid = l.match(/\b(wallet|account|acct|vendor)[-\s#]*([a-z0-9-]+)/);
    return finish({
      targetId: tid ? tid[1] + '-' + tid[2] : undefined,
      source: 'prompt', raw: t, text: l,
      agent: ctx.agent, status: ctx.status,
      type, target, table, value, count, snapshot, irreversible, conf,
      explicitUnhealed: null, recent: [], notes: [],
    });
  }

  /* ---------- structured tool calls ----------
     An agent's real tool call arrives as a tool name and arguments, so nothing has to be guessed from a sentence:
     the amount, count, table and backup flag come straight from the arguments. A readable description is still
     built so that the wording rules (credentials, impersonation, ...) check any free text in the call. */
  const TOOL_TYPES = { transfer_funds: 'transfer_funds', approve_payment: 'approve_payment', process_refund: 'process_refund', send_email: 'send_bulk_email',
    delete_records: 'delete_record', export_data: 'export_data', update_record: 'update_field', read_data: 'read_data', cancel_subscription: 'cancel_subscription', create_record: 'create_record' };
  function describeTool(tool, g) {
    const n = x => (x == null ? '' : x);
    switch (tool) {
      case 'transfer_funds': return `Transfer ₹${n(g.amount)} to ${n(g.to)} ${n(g.note)}`;
      case 'approve_payment': return `Approve payment of ₹${n(g.amount)} to ${n(g.to)} ${n(g.note)}`;
      case 'process_refund': return `Refund ₹${n(g.amount)} for order ${n(g.order)} ${n(g.note)}`;
      case 'send_email': return `Send email to ${n(g.recipients)} recipients ${n(g.list)}: ${n(g.subject)} ${n(g.body)}`;
      case 'delete_records': return `Delete ${n(g.count)} records from ${n(g.table)} ${g.backup === false ? 'with no backup' : g.backup ? 'with a backup' : ''} ${n(g.note)}`;
      case 'export_data': return `Export ${n(g.dataset)} data to ${n(g.destination)} ${n(g.note)}`;
      case 'update_record': return `Update ${n(g.field)} on ${n(g.table)} ${n(g.id)} to ${n(g.value)}`;
      case 'cancel_subscription': return `Cancel subscription ${n(g.id)}`;
      case 'create_record': return `Create ${n(g.table)} record ${n(g.note)}`;
      default: return `Read ${n(g.dataset || g.table)} ${n(g.note)}`;
    }
  }
  function fromStructured(sc, ctx) {
    const tool = String(sc.tool || ''), g = sc.args || {};
    let type = TOOL_TYPES[tool] || 'unknown_action';
    const num = x => { const v = parseFloat(String(x == null ? '' : x).replace(/[^\d.]/g, '')); return isFinite(v) ? v : 0; };
    const count = g.count != null ? num(g.count) : g.recipients != null ? num(g.recipients) : null;
    if (type === 'delete_record' && count != null && count > 5) type = 'bulk_delete';
    if (tool === 'send_email' && count != null && count <= 1) type = 'send_email';
    const raw = describeTool(tool, g).replace(/\s+/g, ' ').trim();
    const snapshot = g.backup === false ? false : g.backup === true ? true : (ctx.snapshot !== false);
    const a = {
      source: 'tool', raw, text: raw.toLowerCase(), agent: ctx.agent, status: ctx.status,
      type, target: g.table || g.to || g.dataset || 'record', table: g.table || null, targetId: g.to || g.id || undefined,
      value: num(g.amount), count, snapshot, irreversible: type === 'send_bulk_email' || g.irreversible === true,
      conf: TOOL_TYPES[tool] ? 0.99 : 0.5, explicitUnhealed: null, recent: [], notes: [], structured: { tool, args: g },
    };
    return finish(a);
  }

  function fromTest(tc) {
    const i = tc.input, p = i.params || {};
    let type = i.action_type;
    const text = (JSON.stringify(p) + ' ' + (i.declared_intent || '') + ' ' + (i.target || '')).toLowerCase();
    const known = ACTIONS[type] && type !== '_unknown';
    const recent = (i.recent_actions_in_window || []).concat(i.new_action ? [i.new_action] : [])
      .map(x => ({ label: x.id, target: x.target_id, value: x.value_at_risk_inr || 0 }));
    const a = {
      source: 'test', raw: tc.name, text, testId: tc.id,
      agent: i.agent_id, status: i.agent_status || 'ACTIVE',
      acl: LEVEL_NUM[i.acl] || 6.5, aal: LEVEL_NUM[i.aal] || 6.5,
      type, target: i.target || i.target_id || 'record', table: null,
      value: i.value_at_risk_inr || (i.new_action ? i.new_action.value_at_risk_inr : 0) || 0,
      count: p.record_count || p.recipient_count || null,
      snapshot: i.snapshot_available !== false,
      irreversible: !!i.truly_irreversible,
      conf: i.classification_confidence != null ? i.classification_confidence : (known ? 0.97 : 0.5),
      explicitUnhealed: i.dependents_not_fixed_by_restore != null ? i.dependents_not_fixed_by_restore : 0,
      govId: p.document_type === 'aadhaar', idLike: !!p.flagged_as_id_like,
      recent, notes: [],
      structThreshold: i.structuring_threshold_inr,
      targetId: i.target_id,
    };
    if (i.compliance_risk_note) a.notes.push(i.compliance_risk_note);
    return finish(a);
  }

  function finish(a) {
    if (!a._tf) {
      a._tf = true;
      const fx = fixTypos(a.text || ''); if (fx !== a.text) a.text = a.text + ' ' + fx;
      const nv = normalizeText(a.source === 'doc' ? a.text : (a.raw || a.text)); if (nv.length) a.text = a.text + ' ' + nv.join(' ');
    }
    const meta = ACTIONS[a.type] || ACTIONS._unknown;
    a.meta = meta; a.cat = meta.cat;
    a.injection = /(ignore|disregard|forget).{0,25}(previous|prior|above|earlier|all).{0,25}(instruction|rule|prompt|polic)|you are now|system prompt|override (the )?(polic|guardrail|safety)/.test(a.text);
    // document stems (not whole words) so minor typos like "lettr", "certificat" still match
    const FORGE_WORD = '(fake|fak\\w*|forg\\w*|fraud\\w*|counterfeit\\w*|fabricat\\w*|bogus|duplicate|spoof\\w*)';
    const DOC_WORD = '(lett\\w*|certificat\\w*|documen\\w*|id\\s*card|identity\\s*card|degree|diplom\\w*|marksheet|mark\\s*sheet|invoic\\w*|payslip|pay\\s*slip|offer|appointment|experience|noc|letterhead|signatur\\w*|stamp|seal)';
    a.harmful = /(launder|bypass (kyc|verification|the guardrail)|evade|exfiltrate|steal|hide .{0,20} from (the )?audit|fake (invoice|identity))/.test(a.text) ||
      /\b(make|build|synthesi[sz]e|manufacture|assemble)\b.{0,25}\b(bombs?|explosives?|nerve agent|bioweapon|chemical weapon|untraceable (gun|firearm)|ghost gun)\b|\b(kill|poison|murder|assassinate|hurt)\b.{0,20}\b(someone|somebody|people|a person|my (wife|husband|boss|neighbou?r|ex)|him|her|them)\b|\b(suicide|kill myself|self.harm)\b/.test(a.text) ||
      new RegExp(FORGE_WORD + '.{0,30}' + DOC_WORD).test(a.text) ||
      new RegExp(DOC_WORD + '.{0,30}' + FORGE_WORD).test(a.text);
    if (a.govId == null) a.govId = /(aadhaar|aadhar|passport|pan card|voter id|government id|driving licen[cs]e)/.test(a.text) && a.type === 'edit_document';
    if (a.idLike == null) a.idLike = false;
    // Any request to produce an official-looking credential/employment document — offer,
    // appointment or experience letter, certificate, marksheet, payslip, ID card, NOC —
    // is blocked by default, whether or not it says "fake": generating these at all is the
    // risk (job-fraud, visa-fraud, loan-fraud use cases), not just a dishonest phrasing of it.
    // The only way through is an explicit, clearly-labelled sample/template/dummy request.
    const OFFICIAL_DOC = /((offer|appointment|experience|relieving|joining|employment|promotion)\s*lett\w*|lett\w*\s*(of|for)\s*(an?\s+)?(offer|appointment|experience|relieving|joining|employment|promotion)|certificat\w*|\bdegree\b|diplom\w*|marksheet|mark\s*sheet|payslip|pay\s*slip|id\s*card|identity\s*card|\bnoc\b|letterhead)/;
    const SAMPLE_OK = /(sample|template|generic|dummy|placeholder|example|mock|specimen|fictional|made.?up|fictitious|for (reference|practice|learning|demo|training purposes))/;
    a.officialDocRequest = OFFICIAL_DOC.test(a.text) && !SAMPLE_OK.test(a.text);
    return a;
  }

  /* ---------- pipeline stages ---------- */
  function analyze(a) {
    const scope = a.type === 'bulk_delete' ? `${a.count || 'many'} records`
                : a.type === 'send_bulk_email' ? `${a.count ? a.count.toLocaleString('en-IN') : 'a list of'} recipients`
                : 'single record';
    const tau = a.cat === 'read' ? 'session' : (a.cat === 'money' || a.cat === 'comms') ? 'one-shot, expires on review' : 'until review decision';
    const pi = a.cat === 'read' ? 'access log' : a.snapshot ? 'audit log + snapshot id' : 'audit log (no snapshot)';
    return {
      c: [a.agent, a.target],            // who is in the chain
      r: a.meta.verb,                    // operation
      e: a.table ? `${a.table}/*` : a.target,   // endpoint
      a: a.value ? { value_at_risk_inr: a.value, count: a.count } : { count: a.count },
      s: scope,                          // scope
      tau, pi,                           // τ valid temporarily · π prove later
    };
  }

  const safetyShort = a => (a && a.wg && a.wg.short) || 'WildGuard';
  function wildguard(a) {
    const unsafe = a.injection || a.harmful;
    const forgery = a.harmful && /(fak\w*|forg\w*|fraud\w*|counterfeit\w*|fabricat\w*|bogus|duplicate|spoof\w*)/.test(a.text) &&
      /(lett\w*|certificat\w*|documen\w*|id\s*card|identity\s*card|degree|diplom\w*|marksheet|mark\s*sheet|invoic\w*|payslip|pay\s*slip|offer|appointment|experience|noc|letterhead|signatur\w*|stamp|seal)/.test(a.text);
    // Real WildGuard verdict (a.wg) when the server reached a hosted model; it can only add to the
    // keyword result, never clear it: the keyword rules stay as a safety net.
    if (a.wg && a.wg.ok) {
      let wgUnsafe = !!a.wg.harmful_request;
      // WildGuard is a content-safety model, so it also flags plain admin commands ("delete this account").
      // On an ordinary, recognised business operation its flag sends the action to a human (REVIEW);
      // it blocks outright only for harmful content, injection, forgery, or anything that isn't such an operation.
      const operational = ['delete', 'update', 'money', 'comms', 'create', 'export'].includes(a.cat) && a.conf >= 0.8;
      // WildGuard also flags routine words ("audit report", "KYC status"). On its own that is not enough to block: a flag
      // that nothing else supports (a rule, the policy model, a harmful-content keyword) sends the request to a person.
      const pm = a.policyCheck && a.policyCheck.ok && a.policyCheck.model;
      const corroborated = unsafe || violentWording(ut(a)) || HARD_RULES.some(r => r.test(a)) || !!(pm && pm.rule && pm.rule !== 'none' && pm.p >= POLICY.modelReview);
      // A read-only message that merely contains the person's own sensitive data ("my SSN is ...") is flagged as sensitive data on the page,
      // not sent to a person because the safety model disliked it: nothing runs, and the agent's answer is screened and masked.
      if (wgUnsafe && a.cat === 'read' && !corroborated && detectPII(a.raw).matches.some(m => m.type !== 'Phone')) wgUnsafe = false;
      const lone = wgUnsafe && !corroborated;                       // a flag nothing else supports
      const concern = wgUnsafe && !unsafe && (corroborated ? operational : POLICY.wgUncorroborated >= 3);
      const hardUnsafe = unsafe || (wgUnsafe && !operational && corroborated);
      return {
        unsafe: hardUnsafe, concern, conf: wgUnsafe || unsafe ? 0.94 : 0.9,
        label: a.injection ? 'prompt-injection attempt' : forgery ? 'document forgery request' : a.harmful ? 'harmful request' : concern ? 'possible harm, on an ordinary business operation' : wgUnsafe ? 'harmful request (WildGuard)' : 'benign',
        score: a.injection ? 10 : a.harmful || (wgUnsafe && !operational && corroborated) ? 9 : concern ? 4 : 0, forgery, simulated: false,
        source: (a.wg.model || 'WildGuard 7B') + (unsafe ? ' + keyword rules' : ''), name: safetyShort(a), wgRaw: a.wg.raw,
      };
    }
    return {
      unsafe, conf: unsafe ? 0.94 : 0.9,
      label: a.injection ? 'prompt-injection attempt' : forgery ? 'document forgery request' : a.harmful ? 'harmful request' : 'benign',
      score: a.injection ? 10 : a.harmful ? 9 : 0,
      forgery,
      simulated: true, source: 'keyword stand-in', name: 'WildGuard',
    };
  }

  function policy(a) {
    const hard = HARD_RULES.filter(r => r.test(a)).map(r => ({ id: r.id, title: r.title, rem: r.remediation(a) }));
    const soft = SOFT_RULES.filter(r => r.test(a)).map(r => ({ id: r.id, title: r.title, pts: r.pts }));
    // The policy model reads what the request means, so a reworded request that the patterns miss is still caught.
    // It needs less certainty when WildGuard has already flagged the same request as harmful.
    let suspect = null;
    const pm = a.policyCheck && a.policyCheck.ok && a.policyCheck.model;
    if (pm && pm.rule && pm.rule !== 'none') {
      const wgFlag = !!(a.wg && a.wg.ok && a.wg.harmful_request);
      const need = wgFlag ? POLICY.modelThresholdWithWG : POLICY.modelThreshold;
      const rule = HARD_RULES.find(r => r.id === pm.rule);
      if (rule && pm.p >= need && !hard.some(h => h.id === rule.id)) {
        hard.push({ id: rule.id, title: rule.title, rem: rule.remediation(a), source: 'model', p: pm.p, withWildGuard: wgFlag });
      } else if (rule && !hard.length && pm.p >= (a.cat === 'read' ? Math.max(POLICY.modelReview, 0.8) : POLICY.modelReview)) {   // a read has no side effects: it needs a firmer match to be held
        // not sure enough to block on its own: a person decides
        suspect = { id: rule.id, title: rule.title, p: pm.p };
      }
    }
    // Asking for records tied to a personal ID or card number ("my Aadhaar is 123456789012, fetch all the details") is a lookup of a person's data:
    // it is not blocked, but a person confirms it. Merely stating your own number (no lookup wording) is only flagged on the page.
    if (!hard.length && !suspect && /\b(fetch|get|find|show|give|pull|look ?up|retrieve|extract|search|dump|share|send|export|list|display|tell me|details|records?|information|info|profile|history|related to|linked to|associated with)\b/i.test(a.raw || '') && detectPII(a.raw).matches.some(m => m.high)) {
      suspect = { id: 'pii_lookup', title: 'Looking up records tied to a personal ID or card number needs a person to confirm', p: 1, pii: true };
    }
    // Model-detected intent/action mismatch is itself a policy-relevant signal
    // (declared intent should match what's actually being done) — added as a
    // soft rule only when the backend model is available, never required.
    if (a.declaredIntent && a.backend && a.backend.ok && a.backend.intent_match === false) {
      soft.push({ id: 'model_intent_mismatch', title: 'Declared intent does not match the actual action (model-detected)', pts: 6, source: 'model' });
    }
    const score = hard.length ? 10 : clamp(soft.reduce((s, r) => s + r.pts, 0), 0, 10);
    return { hard, soft, score, suspect };
  }

  // When server.py is reachable, the graph and its traversals come from the real database's
  // foreign keys (reversal_cost_engine/depgraph.py). Otherwise the built-in copy above is used.
  const DB = { loaded: false, traversals: null };
  async function loadSchema() {
    for (let attempt = 0; attempt < 3 && !DB.loaded; attempt++) {
      try {
        const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
        const timer = ctrl ? setTimeout(() => ctrl.abort(), 4000) : null;
        const r = await fetch('/api/schema', { signal: ctrl ? ctrl.signal : undefined });
        if (timer) clearTimeout(timer);
        const j = await r.json();
        if (j.ok) { GRAPH.edges = j.edges; GRAPH.tables = j.tables; DB.traversals = j.traversals; DB.loaded = true; }
        else if (r.status === 503) break;     // sqlalchemy missing: don't keep asking
      } catch (e) { /* server not reachable yet, or still warming up */ }
      if (!DB.loaded && attempt < 2) await new Promise(res => setTimeout(res, 1000));
    }
    return DB.loaded;
  }

  function traverse(root) {
    if (DB.loaded && DB.traversals[root]) {
      const reached = { [root]: { count: 1, depth: 0, heals: null } };
      Object.entries(DB.traversals[root]).forEach(([t, v]) => { reached[t] = { count: v.count, depth: v.depth, heals: v.heals }; });
      const list = Object.entries(reached).filter(([t]) => t !== root);
      return { mode: 'graph', root, reached, source: 'database',
               total: list.reduce((s, [, v]) => s + v.count, 0), unhealed: list.filter(([, v]) => !v.heals).reduce((s, [, v]) => s + v.count, 0) };
    }
    const reached = { [root]: { count: 1, depth: 0, heals: null } };
    const queue = [root];
    while (queue.length) {
      const p = queue.shift();
      for (const e of GRAPH.edges.filter(e => e.parent === p)) {
        const n = GRAPH.rows[`${p}>${e.child}`] || 0;
        if (!n) continue;
        if (!reached[e.child]) { reached[e.child] = { count: 0, depth: reached[p].depth + 1, heals: e.heals }; queue.push(e.child); }
        reached[e.child].count += n;
      }
    }
    const list = Object.entries(reached).filter(([t]) => t !== root);
    const total = list.reduce((s, [, v]) => s + v.count, 0);
    const unhealed = list.filter(([, v]) => !v.heals).reduce((s, [, v]) => s + v.count, 0);
    return { mode: 'graph', root, reached, total, unhealed, source: 'built-in demo graph' };
  }

  function dependents(a) {
    // graph-traversal mode when the target resolves to a table in the schema
    const root = a.table && (a.cat === 'delete' || a.type === 'cancel_subscription') && a.explicitUnhealed == null ? a.table : null;
    if (root) return traverse(root);
    let unhealed;
    if (a.explicitUnhealed != null) unhealed = a.explicitUnhealed;
    else if (a.count && (a.cat === 'delete' || a.type === 'send_bulk_email')) unhealed = Math.round(a.count);
    else if (a.type === 'delete_record') unhealed = 6;
    else if (a.type === 'bulk_delete') unhealed = 40;
    else unhealed = 0;
    return { mode: 'count', total: unhealed, unhealed };
  }

  function reversal(a, dep) {
    if (a.irreversible) return { score: 10, unbounded: true, restore: 10, side: 0, dep };
    // money that has left the account cannot be restored from a snapshot
    const restore = a.type === 'edit_financial_figure' ? (a.snapshot ? 0 : 4) : (a.cat === 'money' && a.value > 0 && a.value < 1000) ? 1 : (a.cat === 'money' || a.cat === 'comms') ? 4 : (a.cat === 'read' ? 0 : (a.snapshot ? 0 : 4));
    const side = Math.min(6, (dep.unhealed / 6) * 6);
    return { score: r1(Math.min(10, restore + side)), unbounded: false, restore, side: r1(side), dep };
  }

  // LOW/MID/HIGH from the trained classifier -> an anchor point on the 0-10 scale,
  // same band centers the hand-set heuristic already aims for.
  const IMPACT_LABEL_ANCHOR = { LOW: 1.5, MID: 5, HIGH: 8.5 };
  const MODEL_MIN_CONF = 0.6;
  const MODEL_MAX_LOWER = 2.5;

  function impact(a) {
    const valTerm = Math.min(3, Math.log10(1 + a.value / 100)) * 1.5;
    const base = (a.cat === 'money' && a.type !== 'edit_financial_figure' && a.value > 0 && a.value < 1000) ? 2 : a.meta.base;
    const heuristicScore = r1(clamp(base + valTerm, 0, 10));
    const parts = { base, value: r1(valTerm) };
    if (a.backend && a.backend.ok && a.backend.impact && a.cat !== 'read') {      // a read-only request has no impact to rate
      const conf = a.backend.confidence;
      // A low-confidence prediction is ignored, not averaged in.
      if (conf != null && conf < MODEL_MIN_CONF) {
        return { score: heuristicScore, parts, source: 'heuristic', note: `model unsure (${Math.round(conf * 100)}% < ${Math.round(MODEL_MIN_CONF * 100)}%), ignored`,
                 model: { impact: a.backend.impact, confidence: conf, probs: a.backend.probs || null } };
      }
      // The model leads when it is sure: weight 0.5 (60-70% sure), 0.65 (70-85%), 0.8 (85%+) on its LOW/MID/HIGH anchor.
      // It may raise the estimate freely but lower it by at most MODEL_MAX_LOWER, because under-rating a risk is the costly mistake.
      // Measured on 143 hand-written prompts this made the impact stage right 92% of the time (53% with rules alone).
      const anchor = IMPACT_LABEL_ANCHOR[a.backend.impact] != null ? IMPACT_LABEL_ANCHOR[a.backend.impact] : heuristicScore;
      const w = conf == null ? 0.5 : conf >= 0.85 ? 0.8 : conf >= 0.7 ? 0.65 : 0.5;
      const blended = r1(clamp(Math.max(w * anchor + (1 - w) * heuristicScore, heuristicScore - MODEL_MAX_LOWER), 0, 10));
      return {
        score: blended, parts,
        source: 'model+heuristic',
        model: { impact: a.backend.impact, intent_match: a.backend.intent_match, confidence: a.backend.confidence, probs: a.backend.probs || null },
      };
    }
    if (a.backend && a.backend.skipped) return { score: heuristicScore, parts, source: 'heuristic', note: a.backend.skipped };
    return { score: heuristicScore, parts, source: 'heuristic' };
  }

  // Required autonomy for the action itself (0-10)
  function requiredLevel(a) {
    let r = a.meta.req;
    if (a.cat === 'money' && a.value > 0 && a.value < 1000) r = Math.min(r, 4.5);
    return r;
  }

  /* Both levels are numbers on a 0-10 scale.
     ACL = Allowed Capability Level: how well-equipped the agent is for THIS action.
     AAL = Allowed Autonomy Level: how much unsupervised autonomy the session has earned.
     The ceiling is min(AAL, ACL); capability alone never unlocks permission. */
  function authorize(a, history) {
    const req = requiredLevel(a);
    const f = { acl: [], aal: [] };
    let acl, aal;
    if (typeof a.acl === 'number') { acl = a.acl; f.acl.push(['Supplied by the test case', acl]); }
    else {
      const understand = 5 * a.conf;
      const undo = (a.snapshot || a.cat === 'read') ? 3 : 0;
      const scope = (a.cat === 'delete' || a.cat === 'comms') ? 2 * (1 - Math.min(1, (a.count || 1) / 200)) : 2;
      acl = r1(clamp(understand + undo + scope, 0, 10));
      f.acl.push([`Understanding of the request (${Math.round(a.conf * 100)}% confident)`, r1(understand)],
                 [(a.snapshot || a.cat === 'read') ? 'An undo path exists for this target' : 'No undo path for this target', undo],
                 [a.count > 1 && (a.cat === 'delete' || a.cat === 'comms') ? `Scope is wide (${a.count} items)` : 'Scope is narrow', r1(scope)]);
    }
    if (typeof a.aal === 'number') { aal = a.aal; f.aal.push(['Supplied by the test case', aal]); }
    else {
      const h = (history || []).slice(-5);
      let streak = 0; for (let i = h.length - 1; i >= 0 && h[i].verdict === 'ALLOW'; i--) streak++;
      const blocks = h.filter(x => x.verdict === 'BLOCK').length, reviews = h.filter(x => x.verdict === 'REVIEW').length;
      const flagged = h.some(x => x.unsafe), lastTR = h.length ? (h[h.length - 1].tr || 0) : 0;
      const parts = [['Starting autonomy for a new session', 6],
        ['Earned by clean actions in a row (+0.4 each, up to 5)', r1(0.4 * Math.min(5, streak))],
        ['Lost to blocked actions (−1.2 each)', r1(-1.2 * blocks)], ['Lost to actions sent to review (−0.3 each)', r1(-0.3 * reviews)],
        ['Lost to a flagged unsafe request (−1.5)', flagged ? -1.5 : 0], ['Lost to recent trajectory risk (−2 × TR)', r1(-2 * lastTR)]];
      // Floor of 1.5: a bad run costs autonomy but never locks the agent out of even small, reversible actions.
      aal = r1(clamp(parts.reduce((x, p) => x + p[1], 0), 1.5, 10));
      f.aal = parts;
    }
    const ceiling = r1(Math.min(acl, aal));
    return { acl, aal, ceiling, req, gap: ceiling < req, capableOnly: acl > aal, factors: f };
  }

  /* ---------- trajectory risk engine (docx §2-7) ---------- */
  const SEQ_RISK = (prev, cur) => {
    const t = prev.cat + '>' + cur.cat;
    const T = { 'read>export': 3, 'read>comms': 2.5, 'update>delete': 2, 'delete>delete': 1.5, 'money>money': 1.2,
                'export>comms': 3, 'update>money': 1.5, 'delete>money': 2 };
    return T[t] != null ? T[t] : 0.3;
  };

  function trajectory(history, cfg) {
    const k = cfg.traj;
    const rs = history.map(h => h.r);
    const n = rs.length;
    const C = rs.reduce((s, x) => s + x, 0);
    const win = (arr) => {
      let s = 0, wsum = 0;
      for (let j = 0; j < Math.min(k.window, arr.length); j++) { const w = Math.pow(k.lambda, j); s += w * arr[arr.length - 1 - j]; wsum += w; }
      return { s, wsum };
    };
    const W = win(rs), Wp = win(rs.slice(0, -1));
    let D = 0;
    for (let i = 1; i < n; i++) {
      D += SEQ_RISK(history[i - 1], history[i]);
      if (history[i].target && history[i].target === history[i - 1].target) D += 1.5;  // same-target repetition
    }
    const V = n > 1 ? (W.s - Wp.s) : W.s;
    const norm = {
      C: clamp(C / k.budget, 0, 1),
      W: n ? clamp(W.s / (10 * W.wsum), 0, 1) : 0,
      D: clamp(D / 12, 0, 1),
      V: clamp(V / 5, 0, 1),
    };
    const TR = k.wA * norm.C + k.wB * norm.W + k.wC * norm.D + k.wD * norm.V;
    const decision = TR >= k.tau2 ? 'BLOCK' : TR >= k.tau1 ? 'HITL' : 'ALLOW';
    return { n, C: r1(C), W: r1(W.s), D: r1(D), V: r1(V), norm, TR: Math.round(TR * 100) / 100, decision, series: rs };
  }

  function structuring(a, cfg, derived) {
    const k = cfg.traj;
    const win = (a.recent.length ? a.recent : (derived || [])).slice(-k.structWindow);
    if (win.length < 3) return null;
    const last = win[win.length - 1];
    const same = win.filter(x => x.target && x.target === last.target);
    const sum = same.reduce((s, x) => s + x.value, 0);
    const thr = a.structThreshold || k.structThreshold;
    return same.length >= 3 && sum > thr ? { count: same.length, sum, thr, target: last.target } : null;
  }

  /* ---------- explanation / counterfactual ---------- */
  function explain(a, res) {
    const alts = [], rs = res.reasons;
    let retry = null;
    if (res.policy.hard.length) {
      const h = res.policy.hard[0];
      return { reason: `Hard policy rule “${h.id}” applies: ${h.title}.`,
        remediation: h.rem, alternatives: h.rem.possible ? h.rem.steps : ['No agent-side remediation exists. Hand this to a human operator through the official process.'], retry: null };
    }
    if (res.wild.forgery) alts.push('I can’t help produce a document that impersonates a real organisation (Infosys or any other named company) — that’s a forgery risk, not something sanitising the input fixes. I can draft a generic, clearly-labelled sample/template instead, with placeholder names and a "SAMPLE — not a real document" watermark.');
    else if (res.wild.concern) alts.push(`Have a person confirm this request. ${res.wild.name || 'WildGuard'} rated it possibly harmful, although it looks like an ordinary business operation. If it is legitimate, they can approve it.`);
    else if (res.wild.unsafe) alts.push('Strip the untrusted instruction text from the field and re-submit only the sanitised value.');
    if (res.rev.unbounded) alts.push(a.type === 'send_bulk_email'
      ? 'Send to a small pilot segment first and keep a recall window; confirm consent on the full list before widening.'
      : 'Use a reversible path (hold / pending state) instead of an immediate irreversible action, and require a named approver.');
    if (!a.snapshot && a.cat !== 'read' && !res.rev.unbounded) {
      alts.push('Take a snapshot first, then soft-delete (archive) instead of hard delete.');
      retry = { prompt: a.raw.replace(/\b(delete|remove|drop|erase|destroy)\b/i, 'archive (soft-delete)') + ' — with a snapshot taken first', patch: { snapshot: true } };
    }
    if (a.type === 'bulk_delete') alts.push('Split into batches of ≤ 20 records, each reviewed before the next runs.');
    if (res.dep.unhealed > 0 && !res.rev.unbounded) alts.push(`Detach or re-point the ${res.dep.unhealed} dependent record(s) that a restore would not heal before acting.`);
    if (res.auth.gap) alts.push(`Route to a human, or wait until the agent has earned autonomy of at least ${res.auth.req} (the ceiling is ${res.auth.ceiling} now). Autonomy is earned through clean actions, so an agent cannot raise its own ceiling.`);
    if (res.structuring) alts.push('Combine the grouped actions into one request so the total is reviewed against the threshold.');
    if (a.conf < 0.6) alts.push('Re-submit using a recognised action type, or add a clearer declared intent.');
    if (res.policy.soft.length) alts.push(`Attach evidence for “${res.policy.soft[0].id}” (${res.policy.soft[0].title.toLowerCase()}).`);
    if (!alts.length) alts.push('No change needed — proceed.');
    return { reason: rs[0] || 'Action is within every limit.', remediation: null, alternatives: alts, retry };
  }

  /* ---------- the whole pipeline ---------- */
  /* ---------- sensitive data detector ----------
     Finds personal and secret data typed into a request (SSN, email, phone, card, Aadhaar, PAN, API keys) and says where it is,
     so the page can highlight it. It is informational: it does not change the verdict by itself (the rules and models decide that),
     and the ledger masks the same kinds of data before anything is saved. No lookbehind, so it also runs in older browsers. */
  const luhn = (digits) => { let sum = 0, dbl = false; for (let i = digits.length - 1; i >= 0; i--) { let d = digits.charCodeAt(i) - 48; if (dbl) { d *= 2; if (d > 9) d -= 9; } sum += d; dbl = !dbl; } return sum % 10 === 0; };
  const PII_KINDS = [   // in priority order: when two matches overlap, the earlier kind wins
    { type: 'API key',     re: /\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gsk_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|xox[abp]-[A-Za-z0-9-]{10,})\b/g, high: true },
    { type: 'Email',       re: /[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/g },
    { type: 'SSN',         re: /\b\d{3}-\d{2}-\d{4}\b/g, high: true },
    { type: 'Credit Card', re: /\b(?:\d[ -]?){12,18}\d\b/g, high: true, ok: m => { const d = m.replace(/\D/g, ''); return d.length >= 13 && d.length <= 19 && luhn(d); } },
    { type: 'Aadhaar',     re: /\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b(?![ -]?\d)/g, high: true, ok: (m, src, i) => !/\d[ -]$/.test(src.slice(Math.max(0, i - 2), i)) },
    { type: 'PAN',         re: /\b[A-Z]{5}\d{4}[A-Z]\b/g, high: true },
    { type: 'Phone',       re: /(?:\+91[-\s]?|\b)[6-9]\d{4}[-\s]?\d{5}\b|(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s?|\b\d{3}[-.\s])\d{3}[-.\s]\d{4}\b/g,
      ok: (m, src, i) => !/(account|acct|a\/c|order|invoice|ticket|txn|transaction|ref(?:erence)?|record|customer|user|employee|case)\s*(?:no\.?|number|id|#)?\s*[:#-]?\s*$/i.test(src.slice(Math.max(0, i - 24), i)) },
  ];
  // a number with no separators proves nothing alone, but the person named it ("my Aadhaar number is 123456789012", "SSN 123456789")
  const PII_NAMED = [
    { type: 'Aadhaar',     re: /\b(?:aadhaar|aadhar|adhaar|adhar|uidai?)\b[^\d]{0,30}?(\d{4}[ -]?\d{4}[ -]?\d{4})\b(?![ -]?\d)/gi, high: true },
    { type: 'SSN',         re: /\b(?:ssn|social security(?: number)?)\b(?:\s+(?:is|number|no\.?))*[^\d]{0,20}?(\d{3}-?\d{2}-?\d{4})\b(?![ -]?\d)/gi, high: true },
    { type: 'Credit Card', re: /\b(?:credit|debit|card)\b[^\d]{0,25}?((?:\d[ -]?){12,18}\d)\b/gi, high: true },
    { type: 'PAN',         re: /\bpan(?:\s+(?:card|number|no\.?))?(?:\s+is)?[^A-Za-z0-9]{0,15}([A-Za-z]{5}\d{4}[A-Za-z])\b/gi, high: true },
  ];
  function detectPII(text) {
    const src = String(text || ''), taken = [], matches = [];
    for (const k of PII_KINDS) {
      k.re.lastIndex = 0; let m;
      while ((m = k.re.exec(src))) {
        const start = m.index, end = start + m[0].length;
        if (m[0].length === 0) { k.re.lastIndex++; continue; }
        if (k.ok && !k.ok(m[0], src, start)) continue;
        if (taken.some(t => start < t[1] && end > t[0])) continue;
        taken.push([start, end]); matches.push({ type: k.type, text: m[0], start, end, high: !!k.high });
      }
    }
    for (const k of PII_NAMED) {
      k.re.lastIndex = 0; let m;
      while ((m = k.re.exec(src))) {
        const g = m[1], start = m.index + m[0].lastIndexOf(g), end = start + g.length;
        if (taken.some(t => start < t[1] && end > t[0])) continue;
        taken.push([start, end]); matches.push({ type: k.type, text: g, start, end, high: true });
      }
    }
    matches.sort((x, y) => x.start - y.start);
    const types = {}; matches.forEach(x => { types[x.type] = (types[x.type] || 0) + 1; });
    return { count: matches.length, types, matches, level: !matches.length ? 'none' : matches.some(x => x.high) ? 'high' : 'medium' };
  }

  function evaluate(a, cfg, history) {
    cfg = cfg || DEFAULT_CFG;
    const out = { action: a, reasons: [], skipped: [] };
    const sn = safetyShort(a);
    out.token = analyze(a);
    out.wild = wildguard(a);
    out.policy = policy(a);
    out.dep = dependents(a);
    out.rev = reversal(a, out.dep);
    out.imp = impact(a);
    out.auth = authorize(a, history);
    const w = cfg.w, wsum = w.R + w.P + w.I || 1;
    out.parts = { R: out.rev.score, P: out.policy.score, I: out.imp.score };
    out.score = r1((w.R * out.parts.R + w.P * out.parts.P + w.I * out.parts.I) / wsum);

    // trajectory (session history + this action)
    const entry = { cat: a.cat, target: a.targetId || a.target, type: a.type, r: out.score, label: a.raw, value: a.value };
    const base = a.recent && a.recent.length
      ? a.recent.map(x => ({ cat: a.cat, target: x.target, r: out.score, label: x.label, value: x.value }))
      : (history || []).concat([entry]);
    out.entry = entry;
    out.structuring = structuring(a, cfg, a.recent.length ? null : base.filter(h => h.cat === 'money').map(h => ({ target: h.target, value: h.value })));
    out.traj = trajectory(base, cfg);

    let tier = out.score >= cfg.t2 ? 'HIGH' : out.score >= cfg.t1 ? 'MID' : 'LOW';
    let trigger = tier === 'LOW' ? 'Decision Engine (score below threshold)'
                : tier === 'MID' ? 'Decision Engine (score in MID band)'
                : 'Decision Engine (score above HIGH threshold)';
    const why = [];

    if (a.status === 'SUSPENDED') {
      tier = 'HIGH'; trigger = 'Stage 0 Agent Status Check (SUSPENDED agents rejected before scoring)';
      why.push(`Agent ${a.agent} is SUSPENDED, so nothing it proposes is evaluated.`);
      out.skipped = ['analyzer', 'wildguard', 'risk', 'auth'];
    } else if (out.policy.hard.length) {
      tier = 'HIGH'; trigger = `Hard Policy Check (${out.policy.hard[0].id} rule, short-circuits before scoring)`;
      why.push(`Hard policy “${out.policy.hard[0].id}” forbids this regardless of risk score${out.policy.hard[0].source === 'model' ? ` (recognised by the policy model, ${Math.round(out.policy.hard[0].p * 100)}% sure${out.policy.hard[0].withWildGuard ? `, and ${sn} flagged it too` : ''})` : ''}.`);
    } else {
      if (out.wild.unsafe) { tier = 'HIGH'; trigger = `${sn} (unsafe flag, high confidence) feeding Weighted Risk Engine`; why.push(`${sn} flagged a ${out.wild.label} (${Math.round(out.wild.conf * 100)}% confidence).`); }
      if (out.wild.concern) {
        why.push(`${sn} flagged this as possibly harmful. It looks like an ordinary business operation, so a person should confirm it instead of it being blocked outright.`);
        if (tier === 'LOW') { tier = 'MID'; trigger = `${sn} (flagged an ordinary business operation, so a human confirms)`; }
      }
      if (out.policy.suspect && out.policy.suspect.pii) {
        why.push('The request asks for records tied to a personal ID or card number (Aadhaar, SSN, PAN or card). That is a lookup of a person\u2019s data, so a person should confirm it.');
        if (tier === 'LOW') { tier = 'MID'; trigger = 'Sensitive-data lookup (records tied to a personal ID number, so a human confirms)'; }
      } else if (out.policy.suspect) {
        why.push(`The wording resembles a prohibited action (“${out.policy.suspect.id}”, ${Math.round(out.policy.suspect.p * 100)}% sure). That is not certain enough to block outright, so a person should confirm it.`);
        if (tier === 'LOW') { tier = 'MID'; trigger = `Policy model (looks like “${out.policy.suspect.id}”, so a human confirms)`; }
      }
      if (out.rev.unbounded) {
        tier = 'HIGH';
        if (!out.wild.unsafe) trigger = out.auth.gap ? 'Authorization Layer (AAL ceiling below required) + Decision Engine' : 'Decision Engine (Unbounded reversal cost forces BLOCK regardless of other scores)';
        why.push('The effect is already outside the system (sent / paid out) so reversal cost is unbounded.');
      }
      if (a.conf < 0.6) { tier = 'HIGH'; if (!out.wild.unsafe && !out.rev.unbounded) trigger = 'Stage 3 AI fallback (classification confidence < 0.6 defaults to caution)'; why.push(`Unrecognised action, classified with only ${Math.round(a.conf * 100)}% confidence.`); }
      if (a.conf >= 0.6 && a.conf < 0.8 && tier === 'LOW') { tier = 'MID'; trigger = 'Action Analyzer (request not clearly understood, asks a human to confirm)'; why.push(`I could not tell what operation this is (${Math.round(a.conf * 100)}% confident), so a human should confirm it.`); }
      if (out.auth.gap && tier === 'LOW') {
        tier = 'MID'; trigger = `Authorization Layer (ceiling = min(ACL ${out.auth.acl}, AAL ${out.auth.aal}) = ${out.auth.ceiling}, caps verdict regardless of low risk score)`;
      }
      if (out.auth.gap) why.push(`Allowed ceiling is min(ACL ${out.auth.acl}, AAL ${out.auth.aal}) = ${out.auth.ceiling}, but this action needs ${out.auth.req}${out.auth.capableOnly ? ' (the agent is capable, but has not earned that much autonomy)' : ''}.`);
      if (out.structuring && tier === 'LOW') {
        tier = 'MID'; trigger = `Trajectory / structuring check (${out.structuring.count} actions, combined ₹${out.structuring.sum} > ₹${out.structuring.thr} threshold)`;
      }
      if (out.structuring) why.push(`${out.structuring.count} small actions to ${out.structuring.target} add up to ₹${out.structuring.sum}, above the ₹${out.structuring.thr} limit (structuring pattern).`);
      if (!out.rev.unbounded && out.rev.score >= 4) why.push(`Reversal cost ${out.rev.score}/10: ${!a.snapshot && a.cat !== 'read' ? 'no snapshot to restore from' : 'effects a restore cannot undo'}${out.dep.unhealed ? `, ${out.dep.unhealed} dependent record(s) would not heal` : ''}.`);
      if (out.policy.soft.length) why.push(`Policy: ${out.policy.soft[0].title}.`);
      // Fail closed: if a safety check that should have run did not (it timed out or errored), do not wave the action through.
      if (a.degraded && a.degraded.length && a.cat !== 'read') {
        why.push(`A safety check did not run (${a.degraded.join(', ')}), so a person must confirm this instead of it being allowed automatically.`);
        if (tier === 'LOW') { tier = 'MID'; trigger = `Fail-closed (${a.degraded.join(', ')} did not run, so a human confirms)`; }
      }
      if (!why.length) why.push(tier === 'LOW' ? 'Safe, authorised and cheaply reversible.' : `Weighted risk ${out.score}/10 falls in the ${tier} band.`);
    }
    // When something else decides a BLOCK, the "a person should confirm" note must not be the headline reason.
    if (tier === 'HIGH' && out.wild.concern) { const i = why.findIndex(w => /^(WildGuard|Llama Guard) flagged this as possibly harmful/.test(w)); if (i > 0 || (i === 0 && why.length > 1)) why.push(why.splice(i, 1)[0]); }
    out.pii = detectPII(a.raw);
    out.tier = tier; out.trigger = trigger; out.reasons = why; out.degraded = (a.degraded || []).slice();
    out.verdict = tier === 'LOW' ? 'ALLOW' : tier === 'MID' ? 'REVIEW' : 'BLOCK';
    out.escalation = out.verdict === 'BLOCK' ? 'trajectory' : out.verdict === 'REVIEW' ? 'explanation' : 'none';
    out.entry.verdict = out.verdict; out.entry.unsafe = out.wild.unsafe || !!out.wild.concern; out.entry.tr = out.traj.TR;
    out.explanation = explain(a, out);
    out.pillars = {
      safe:       { ok: !out.wild.unsafe && !out.wild.concern && !out.policy.hard.length && a.status !== 'SUSPENDED', note: out.wild.unsafe || out.wild.concern ? out.wild.label : out.policy.hard.length ? 'hard policy' : 'no unsafe signal' },
      authorized: { ok: !out.auth.gap, note: `ceiling ${out.auth.ceiling} · needs ${out.auth.req}` },
      explainable:{ ok: true, note: 'trace + ledger entry' },
      reversible: { ok: !out.rev.unbounded && out.rev.score < 4, note: out.rev.unbounded ? 'unbounded' : `cost ${out.rev.score}/10` },
    };
    return out;
  }

  /* ---------- optional backend enrichment (trained intent/impact classifier) ----------
     Best-effort only: a short timeout and any failure (offline, server.py not running,
     scikit-learn/joblib missing) silently leaves a.backend unset, and impact()/policy()
     above already fall back to the pure client-side heuristic in that case. Nothing about
     the deterministic simulation changes when this is unavailable — this only upgrades
     the signal when a real trained model is reachable. */
  async function enrich(a, opts) {
    opts = opts || {};
    if (typeof fetch === 'undefined') return a;
    const wgDone = askWildGuard(a, opts);     // safety applies to every request, including questions
    const pcDone = askPolicy(a);              // policy-rule model + sanctions/consent lookups, also for every request
    // A read-only request has nothing to rate: asking the impact model only adds noise.
    if (a.cat === 'read') { a.backend = { ok: false, skipped: 'read-only request, model not consulted' }; await wgDone; await pcDone; return a; }
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), opts.timeoutMs || 2500) : null;
    try {
      const res = await fetch('/api/intent-impact', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        // The UI has no separate "declared intent", so the model sees the person's own words
        // as the action text. intent_match is only meaningful (and only used) when a real
        // declared intent is supplied (a.declaredIntent).
        body: JSON.stringify({ declared_intent: a.declaredIntent || a.raw || '', action_text: a.raw || '' }),
        signal: ctrl ? ctrl.signal : undefined,
      });
      const j = await res.json();
      a.backend = j.ok ? { ok: true, impact: j.impact, intent_match: j.intent_match, confidence: j.confidence, probs: j.probs || null, source: j.source } : { ok: false };
    } catch (e) {
      a.backend = { ok: false };
    } finally {
      if (timer) clearTimeout(timer);
    }
    await wgDone; await pcDone;
    return a;
  }
  async function askPolicy(a) {
    try {
      const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const timer = ctrl ? setTimeout(() => ctrl.abort(), 2500) : null;
      const res = await fetch('/api/policy-check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: a.raw || '' }), signal: ctrl ? ctrl.signal : undefined });
      if (timer) clearTimeout(timer);
      const j = await res.json();
      a.policyCheck = j.ok ? { ok: true, model: j.model, lookups: j.lookups || {} } : { ok: false };
    } catch (e) { a.policyCheck = { ok: false }; }
    return a;
  }

  // Real WildGuard through the server; silently absent when it isn't configured or reachable.
  async function wildguardCall(prompt, response, timeoutMs) {
    if (typeof fetch === 'undefined') return { ok: false };
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs || 4000) : null;
    try {
      const r = await fetch('/api/wildguard', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt, response: response || '' }), signal: ctrl ? ctrl.signal : undefined });
      return await r.json();
    } catch (e) { return { ok: false }; } finally { if (timer) clearTimeout(timer); }
  }
  async function askWildGuard(a, opts) { a.wg = await wildguardCall(a.raw || '', '', (opts && opts.wgTimeoutMs) || 8000); return a; }
  // An LLM's draft checked by WildGuard as the "response" half of the pair.
  async function wildguardOutput(prompt, response) { const j = await wildguardCall(prompt, response, 12000); return j.ok ? { ok: true, harmful: !!j.harmful_response } : { ok: false }; }

  /* ---------- output screening: the same gate, applied to what an LLM wrote ----------
     CLAUDE.md asks the safety layer to flag responses as well as prompts. A model's draft is
     checked for harmful or injected content and for leaked secrets, and personal data in it is
     masked before display. Keyword rules, like the prompt-side WildGuard stand-in. */
  function screenOutput(text) {
    const reasons = []; let out = String(text || '');
    const l = out.toLowerCase();
    if (/(ignore|disregard|forget).{0,25}(previous|prior|above|earlier|all).{0,25}(instruction|rule|prompt|polic)|you are now|override (the )?(polic|guardrail|safety)/.test(l)) reasons.push('the draft contains an injected instruction');
    if (/(launder|evade (the )?(law|police|audit)|exfiltrate|steal (data|credentials|money)|how to (make|build) (a )?(bomb|weapon|malware))/.test(l)) reasons.push('the draft describes a harmful action');
    if (/\b(sk-[A-Za-z0-9]{16,}|gsk_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)/.test(out) || /(password|passwd|api[_ -]?key|secret)\s*[:=]\s*\S{6,}/i.test(out)) reasons.push('the draft leaks a credential');
    const pii = [[/[\w.+-]+@[\w-]+\.[\w.-]+/g, '[email masked]'], [/\b\d{4}\s\d{4}\s\d{4}\b/g, '[Aadhaar masked]'], [/\b[A-Z]{5}\d{4}[A-Z]\b/g, '[PAN masked]'], [/\b(?:\d{4}[ -]){3}\d{4}\b/g, '[card masked]'], [/\b\d{3}-\d{2}-\d{4}\b/g, '[SSN masked]'], [/\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gsk_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,})\b/g, '[API key masked]']];
    let redacted = false;
    pii.forEach(([re, tag]) => { if (re.test(out)) { redacted = true; out = out.replace(re, tag); } re.lastIndex = 0; });
    return { unsafe: reasons.length > 0, reasons, text: out, redacted };
  }

  /* ---------- evidence ledger hash (FNV-1a, for tamper-evident chaining in the demo) ---------- */
  function hash(s) {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h.toString(16).padStart(8, '0');
  }

  // SHA-256 for new ledger entries (crypto.subtle, available on localhost); FNV-1a is the
  // fallback and the algorithm of older entries, which verifyChain() still understands.
  async function hashAsync(s) {
    try {
      if (typeof crypto !== 'undefined' && crypto.subtle) {
        const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
        return { alg: 'sha256', hash: Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('') };
      }
    } catch (e) { /* fall through */ }
    return { alg: 'fnv1a', hash: hash(s) };
  }
  const LEDGER_FIELDS = ['seq', 'ts', 'agent', 'action', 'verdict', 'tier', 'score', 'trigger'];
  const ledgerPayload = row => { const p = {}; LEDGER_FIELDS.forEach(k => { p[k] = row[k]; }); return JSON.stringify(p); };
  // Recomputes every hash from the stored fields and checks each entry points at the one before it.
  async function verifyChain(rows) {
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      if (i > 0 && r.prev !== rows[i - 1].hash) return { ok: false, index: i, seq: r.seq, why: `entry #${r.seq} does not point at the hash of #${rows[i - 1].seq}` };
      const want = (r.alg || 'fnv1a') === 'sha256' ? (await hashAsync(r.prev + ledgerPayload(r))).hash : hash(r.prev + ledgerPayload(r));
      if ((r.alg || 'fnv1a') === 'sha256' && want.length !== 64) return { ok: false, index: i, seq: r.seq, why: 'SHA-256 is unavailable in this browser context' };
      if (want !== r.hash) return { ok: false, index: i, seq: r.seq, why: `entry #${r.seq} was changed after it was written (its hash no longer matches its contents)` };
    }
    return { ok: true, count: rows.length };
  }

  root.VerifAI = { detectPII, NORMALIZE, normalizeText, fixTypos, hashAsync, verifyChain, ledgerPayload, ACTIONS, GRAPH, HARD_RULES, SOFT_RULES, POLICY, POLICY_DEFAULTS, policy, DEFAULT_CFG, fromPrompt, fromStructured, fromTest, finish, traverse, evaluate, enrich, wildguardOutput, loadSchema, screenOutput, hash, clamp, r1 };
})(typeof window !== 'undefined' ? window : globalThis);
