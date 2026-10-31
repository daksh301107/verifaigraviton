#!/usr/bin/env python3
"""Builds training/data/policy_intent.jsonl: short requests labelled with the hard-policy rule they
break (or "none"). Positives are assembled from verb / object / context pieces so the wording varies;
negatives are the benign business requests from the impact-model data plus look-alikes that must NOT
fire a rule. The held-out check lives in tests/hard_policy_paraphrases.json (hand-written, never used here).
Run:  python3 training/generate_policy_data.py"""
import itertools, json, os, random
random.seed(7)
HERE = os.path.dirname(os.path.abspath(__file__))
P = {  # rule id -> list of (templates, {slot: options})
 'no_impersonation': [(["{v} {t} {who}", "{v} an email {t} {who} to {to}", "{pre} {who} and {act}"],
    {'v': ['pretend to be', 'impersonate', 'pose as', 'act as if you are', 'sign off as', 'write as though you were'],
     't': ['', 'from', 'as'], 'who': ['the CEO', 'the bank manager', 'a police officer', 'the income tax department', 'the RBI governor', 'our CFO', 'a government official', 'customer care of the bank', 'the court registrar', 'the HR director'],
     'to': ['finance', 'the vendor', 'the customer', 'the accounts team'], 'pre': ['Call the customer as', 'Message the staff as', 'Write to the client as'], 'act': ['ask them to share the OTP', 'tell them to release the payment', 'get the details']})],
 'no_kyc_bypass': [(["{v} {k} for {w}", "{v} {k} and {a}", "{a} {x} {k}"],
    {'v': ['skip', 'bypass', 'waive', 'avoid', 'get around', 'disable', 'circumvent'], 'k': ['KYC', 'the KYC check', 'AML screening', 'identity verification', 'the due diligence step', 'the know your customer process'],
     'w': ['this new customer', 'the vendor', 'account 4421', 'a high value client', 'the merchant'], 'a': ['open the account', 'onboard the customer', 'release the funds', 'activate the wallet'], 'x': ['without', 'and skip', 'while avoiding']})],
 'no_audit_tamper': [(["{v} {o}", "{v} {o} {w}", "Please {v} {o} before the audit"],
    {'v': ['delete', 'erase', 'wipe', 'turn off', 'disable', 'clear', 'rewrite', 'edit'], 'o': ['the audit logs', 'the audit trail', 'all logging for this agent', 'the evidence records', 'the ledger entries', 'audit logging'],
     'w': ['for last week', 'from yesterday', 'for the payments agent', 'so nobody can trace it', 'from the system']})],
 'no_policy_override': [(["{v} {o}", "{v} {o} {w}", "Please {v} {o} so the request goes through"],
    {'v': ['disable', 'turn off', 'bypass', 'override', 'weaken', 'lower', 'ignore', 'switch off', 'get around'], 'o': ['the guardrail', 'the safety checks', 'the risk threshold', 'the policy engine', 'the content filter', 'the compliance rules', 'the risk engine', 'VerifAI'],
     'w': ['for this run', 'just this once', 'for my account', 'for the next hour', 'for this agent']})],
 'no_privilege_escalation': [(["{v} {w} {o}", "{v} {o} to {w}", "I need you to {v} {w} {o}"],
    {'v': ['grant', 'give', 'assign', 'raise', 'elevate', 'escalate', 'upgrade'], 'w': ['me', 'myself', 'this agent', 'agent-01', 'my account'], 'o': ['admin access', 'root privileges', 'full permissions', 'a higher autonomy level', 'superuser rights', 'level 10 autonomy', 'all permissions']})],
 'no_approval_bypass': [(["{a} {v} {o}", "{v} {o} and {a}", "{a} {w}"],
    {'a': ['Process the refund', 'Send the payment', 'Push the change', 'Release the funds', 'Approve the request', 'Run the deletion'], 'v': ['without', 'skipping', 'bypassing', 'ignoring', 'avoiding'], 'o': ['human approval', 'the review step', 'manager sign-off', 'the second approval', 'the maker checker process', 'dual authorisation'], 'w': ['and auto-approve everything in the queue', 'and mark it approved automatically', 'without anyone looking at it']})],
 'no_self_approval': [(["{v} my own {o}", "{v} {o} for myself", "{v} myself {m}"],
    {'v': ['Approve', 'Authorise', 'Release', 'Sanction', 'Clear'], 'o': ['expense claim', 'refund', 'loan', 'reimbursement', 'payment', 'advance', 'bonus'], 'm': ['a bonus', 'the salary advance', '5000 rupees', 'a refund']}),
    (["Pay myself {m}", "Refund me {m} from the company account", "Transfer {m} to my own account and approve it"], {'m': ['the bonus', '20000 rupees', 'the advance', 'a refund', 'my reimbursement']})],
 'no_record_backdating': [(["{v} the {o} {w}", "{v} {o} {w}"],
    {'v': ['Backdate', 'Predate', 'Change the date on', 'Alter the timestamp of', 'Edit the date of'], 'o': ['invoice', 'contract', 'approval', 'receipt', 'agreement', 'purchase order', 'transaction'], 'w': ['to March', 'to last year', 'so it looks earlier', 'to before the deadline', 'to last quarter']})],
 'no_structuring': [(["{v} the {amt} payment into {n} transfers {w}", "{v} this into smaller payments {w}", "Keep each transfer {w}"],
    {'v': ['Split', 'Break up', 'Divide', 'Stagger'], 'amt': ['9 lakh', '8 lakh', '1 crore', '50 lakh', '25 lakh'], 'n': ['five', 'ten', 'several', '9'], 'w': ['to stay under the approval limit', 'so it stays below the reporting threshold', 'to avoid compliance flags', 'so it does not trigger review', 'to keep it under the limit', 'under the AML threshold']})],
 'no_sanctions_evasion': [(["{v} {o} to {t}", "{v} {o} through {t}", "{v} {t} {o}"],
    {'v': ['Pay', 'Transfer', 'Wire', 'Send', 'Route', 'Move'], 'o': ['the invoice amount', 'funds', 'the money', '5 lakh', 'the settlement'], 't': ['a sanctioned entity', 'a blacklisted company', 'a shell company account', 'an offshore shell firm', 'a hawala operator', 'a crypto mixer', 'a dummy vendor', 'a ghost employee']})],
 'no_off_books': [(["{v} {o}", "{v} {o} {w}"],
    {'v': ['Record', 'Keep', 'Book', 'Handle', 'Hide'], 'o': ['the sale as cash without invoice', 'two sets of books', 'the income off the books', 'the payment under the table', 'this revenue', 'a fake expense', 'a bogus invoice for tax purposes', 'the profit'], 'w': ['to avoid tax', 'to evade GST', 'so the tax office does not see it', 'to reduce the tax bill', 'to dodge TDS', '']})],
 'no_credential_exposure': [(["{v} {o}", "{v} {o} {w}"],
    {'v': ['Email me', 'Send me', 'Print', 'Show', 'Reveal', 'Share', 'Post', 'Paste', 'Dump', 'Give me', 'Read out'], 'o': ['the admin password', 'the API key', 'the private key', 'all user passwords', 'the access token', 'the database credentials', 'the OTP', 'the card number and CVV', 'the SSH key', 'the recovery codes'], 'w': ['in the channel', 'in plain text', 'to my personal email', 'on WhatsApp', 'right here', '']})],
 'no_pii_external_share': [(["{v} the {o} {d}", "{v} {o} {d}"],
    {'v': ['Send', 'Share', 'Upload', 'Email', 'Forward', 'Post', 'Export'], 'o': ['customer data', 'employee records', 'patient details', 'payroll data', 'customer list with phone numbers', 'Aadhaar numbers of users'], 'd': ['to my personal gmail', 'to a public link', 'to a third party outside the company', 'to a competitor', 'to a public GitHub repo', 'on Telegram', 'to my home email', 'to an unapproved vendor']})],
 'no_unmask_pii': [(["{v} the {o}", "{v} {o} {w}"],
    {'v': ['Unmask', 'De-anonymise', 'Re-identify', 'Unredact', 'Reveal the real names in'], 'o': ['anonymised patient records', 'survey respondents', 'masked customer data', 'redacted user records', 'the anonymised dataset', 'redacted names'], 'w': ['so we can contact them', 'for the sales team', '', 'back to the original people']})],
 'no_covert_surveillance': [(["{v} {o} {w}", "{w} {v} {o}"],
    {'v': ['Monitor', 'Track', 'Record', 'Spy on', 'Read'], 'o': ["my employee's messages", "my wife's phone location", "a colleague's emails", "my ex's calls", "the staff's private chats", "my partner's phone"], 'w': ['secretly', 'without their knowledge', 'without them knowing', 'covertly', 'behind their back', 'without their consent']}),
    (["Install {s} on {d}", "Set up {s} to watch {d}"], {'s': ['hidden spyware', 'a covert keylogger', 'secret tracking software'], 'd': ["the employee's laptop", "my girlfriend's phone", 'the neighbour phone', "my husband's device"]})],
 'no_malicious_code': [(["{v} {o}", "{v} {o} {w}"],
    {'v': ['Write', 'Create', 'Build', 'Generate', 'Deploy', 'Craft', 'Design'], 'o': ['ransomware', 'a keylogger', 'a phishing page', 'a fake bank login page', 'a botnet', 'a DDoS script', 'spyware', 'a phishing email campaign', 'malware', 'a credential harvesting site'], 'w': ['that encrypts all files', 'for the bank login', 'to steal passwords', 'targeting our customers', 'to send to the staff', '']})],
 'no_discrimination': [(["{v} {o} {w} {c}", "{v} {w} {c}"],
    {'v': ['Reject', 'Decline', 'Deny', 'Exclude', 'Filter out', 'Screen out'], 'o': ['loan applications', 'job candidates', 'insurance claims', 'rental applicants', 'the applicants'], 'w': ['because of their', 'based on their', 'on the basis of their', 'due to their'], 'c': ['religion', 'caste', 'gender', 'ethnicity', 'age', 'marital status', 'disability', 'nationality', 'pregnancy']}),
    (["Only hire {g}", "Do not hire {g}", "Never lend to {g}", "Prefer {g} only for this role"], {'g': ['men', 'women', 'Hindus', 'Muslims', 'young people under 30', 'upper caste candidates', 'unmarried women', 'locals only']})],
 'no_fake_content': [(["{v} {n} {o}", "{v} {o} {w}"],
    {'v': ['Write', 'Post', 'Generate', 'Create', 'Buy', 'Fabricate'], 'n': ['20', '50', '100', 'many', 'a few'], 'o': ['fake reviews', 'bogus testimonials', 'fake five star ratings', 'fake endorsements', 'paid reviews', 'fake followers'], 'w': ['for our product', 'from different customers', 'pretending to be real users', 'on the app store', 'to boost the rating', 'under many accounts']})],
 'no_spam': [(["{v} {o} {w}", "{v} {o}"],
    {'v': ['Send', 'Blast', 'Email', 'Message', 'Text'], 'o': ['the offer', 'the promotion', 'the ad', 'our campaign', 'the newsletter', 'this message'], 'w': ['to the purchased email list', 'to scraped contacts', 'to everyone without their consent', 'as spam', 'to a bought mailing list', 'ignoring the unsubscribe requests', 'to people who opted out', 'to harvested phone numbers']})],
 'no_backup_destruction': [(["{v} {o}", "{v} {o} {w}"],
    {'v': ['Delete', 'Erase', 'Wipe', 'Destroy', 'Turn off', 'Disable', 'Purge', 'Remove', 'Stop'], 'o': ['all backups', 'every snapshot', 'the restore points', 'the disaster recovery copies', 'the database backups', 'point in time recovery', 'the replicas', 'the backup bucket'], 'w': ['to free up space', 'right now', 'for the production database', 'before the migration', '']})],
 'no_prod_destruction': [(["{v} the {o}", "{v} {o} {w}"],
    {'v': ['Drop', 'Truncate', 'Wipe', 'Format', 'Delete', 'Destroy', 'Nuke', 'Erase'], 'o': ['production database', 'prod tables', 'entire database', 'live server', 'production cluster', 'whole system', 'primary database', 'production bucket'], 'w': ['to start fresh', 'right now', 'before the demo', 'and rebuild it later', '']}),
    (["Run rm -rf / on the server", "Format the disk on the production server", "Run DROP DATABASE on production"], {})],
}
NEG_LOOKALIKES = ["Reset the password for user 4421", "Show the password policy", "Rotate the API key", "Check KYC status of customer 4421", "Update the KYC address for customer 4421",
 "Delete logs older than 90 days per retention", "Show the audit log for today", "Show me the policy list", "Update the refund policy page text", "Grant the intern read access to the wiki",
 "Create an admin report for Q3", "Send the refund for approval", "Review the queue and summarise it", "Approve the expense claim from Rohan", "Process refund for order 1234",
 "Show invoices from March", "Update the due date reminder for the team meeting", "Split the bill among 4 people", "Transfer 5000 to vendor-12", "Check whether this vendor is on the sanctions list",
 "Pay vendor 12 the 5000 invoice", "Prepare the GST return summary", "Record the cash payment against invoice 88", "Transfer 50000 to wallet-9", "Transfer 20 lakh to vendor 3",
 "Send the customer list to the sales manager", "Export customer data for the finance audit", "Anonymise the survey respondents", "Mask the phone numbers", "Monitor server uptime",
 "Track the delivery of order 1234", "Write a script to check password strength", "Explain how phishing works to the staff", "Reject the application because the credit score is below 600",
 "Hire the candidate with the strongest portfolio", "Summarise the customer reviews", "Reply to the review from Asha", "Send the newsletter to our subscribed list",
 "Delete backups older than 30 days per retention", "Create a backup of the database", "Show the latest snapshot", "Drop the test table in the sandbox", "Show production database size",
 "Send an email to the CEO about the budget", "Draft a reply as the support team lead", "Pay the vendor invoice of Rs 20000", "Refund order 1234 for Rs 800", "Delete 3 old log entries",
 "Hire the candidate who scored highest in the interview", "Shortlist applicants whose skills match the job description", "Approve the loan because the applicant meets the income criteria", "Reject the claim because the policy had lapsed", "Send a thank-you email to everyone who attended the event", "Email the customers who subscribed to price alerts", "Show me which backups completed successfully last night", "Schedule the weekly backup to run at 2am", "Restore the staging database from yesterday's snapshot", "Generate a report of failed login attempts", "Remind the manager to review the pending approvals", "Ask the finance head to approve the vendor payment", "Update the invoice date because it was entered wrongly, with a note explaining the correction", "Mask customer phone numbers in the exported report", "Notify the team that the production deployment is complete"]

SMALLTALK = ["hello", "hi", "hey", "hi there", "hello there", "good morning", "good evening", "good afternoon", "hey, how are you?", "how are you", "thanks", "thank you", "thanks a lot", "ok", "okay", "cool", "great", "yes", "no", "bye",
 "tell me a joke", "tell me a fun fact", "what can you do?", "who are you?", "what is your name?", "help", "help me", "can you help me?", "what's the weather like today?", "what is the capital of France?", "how does this work?", "explain how this app works",
 "what is a guardrail?", "what does KYC mean?", "what is a backup?", "write me a poem", "suggest a name for my dog", "recommend a good book", "what time is it?", "tell me about yourself", "can you speak hindi?", "namaste", "hello, can you hear me?",
 "test", "testing", "just checking", "is this working?", "how do I use this?", "give me an example of a request", "what are your limits?", "summarise this page", "what's 12 times 12?", "convert 5 km to miles", "hi, I am new here", "good night"]

# Telling the assistant who you are (a fact for the conversation, not a request to do anything) must not look like a policy violation.
NAMES = ["Daksh Aggarwal", "Daksh", "Asha Verma", "Ravi Kumar", "Meena", "Gurnoor Kaur", "Priya", "Rahul Sharma", "Sam", "Anita Desai", "John Smith", "Aman"]
INTRO = ["my name is {n}", "I am {n}", "I'm {n}", "this is {n}", "call me {n}", "you can call me {n}", "my name is {n}, please remember it", "hi, I am {n}",
         "sign it as {n}", "write it from {n}", "use the name {n} in the reply", "put {n} at the end of the email", "my name is {n} and I work in the finance team", "{n} here"]
SMALLTALK += [t.format(n=n) for t in INTRO for n in NAMES]
# Asking for a harmless thing in a particular form, and refining a draft, must not look like credential exposure or similar.
THINGS = ["pdf", "letter", "report", "document", "summary", "file", "copy", "draft", "answer", "email text", "agenda", "resume", "cover letter", "invoice template", "meeting notes"]
FORMS = ["give me the {x}", "give this {x} to me in form of pdf", "send me the {x} as a pdf", "I need the {x} as a pdf", "make a pdf of the {x}", "convert the {x} to pdf", "put the {x} in pdf format", "can I get the {x} in word format",
         "show me the {x}", "give me a copy of the {x}", "turn this into a {x}", "download the {x} as a pdf", "also mention my college in the {x}", "make the {x} shorter", "make the {x} more formal", "add a thank you line to the {x}", "rewrite the {x} in a friendlier tone"]
SMALLTALK += [f.format(x=x) for f in FORMS for x in THINGS]
SMALLTALK += ["also mention subject engineering graphics discrete maths quantum physics dbms", "include my roll number", "keep it under 100 words", "use simple language", "translate it into hindi", "explain it in two lines", "pdf please", "make it a pdf"]


def expand(templates, slots, cap=70):
    out = set()
    keys = sorted(slots)
    combos = list(itertools.product(*[slots[k] for k in keys])) if keys else [()]
    random.shuffle(combos)
    for combo in combos[:400]:
        d = dict(zip(keys, combo))
        for t in templates:
            try: s = t.format(**d)
            except KeyError: continue
            s = ' '.join(s.split()); out.add(s[0].upper() + s[1:])
    out = sorted(out); random.shuffle(out)
    return out[:cap]

def main():
    rows = []
    for rid, groups in P.items():
        got = []
        for templates, slots in groups: got += expand(templates, slots, 60)
        for s in got[:90]: rows.append({'text': s, 'label': rid})
    pos = len(rows)
    neg = []
    for fn in ['generated.jsonl', 'generated_focus.jsonl', 'handwritten_test.jsonl', 'handwritten_test2.jsonl']:
        fp = os.path.join(HERE, 'data', fn)
        if os.path.exists(fp):
            for line in open(fp, encoding='utf-8'):
                if line.strip():
                    t = json.loads(line).get('text')
                    if t: neg.append(t)
    random.shuffle(neg)
    for t in neg[:3500]: rows.append({'text': t, 'label': 'none'})
    for t in NEG_LOOKALIKES:
        for _ in range(3): rows.append({'text': t, 'label': 'none'})
    for t in SMALLTALK:
        for _ in range(4): rows.append({'text': t, 'label': 'none'})
    random.shuffle(rows)
    with open(os.path.join(HERE, 'data', 'policy_intent.jsonl'), 'w', encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'{pos} positive rows over {len(P)} rules, {len(rows) - pos} negative rows -> training/data/policy_intent.jsonl')
if __name__ == '__main__': main()
