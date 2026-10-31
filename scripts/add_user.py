#!/usr/bin/env python3
"""Adds or updates a user in policy_data/users.csv.  python3 scripts/add_user.py <id> "<name>" <requester|reviewer|admin>"""
import csv, getpass, os, secrets, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vf_auth as A
if len(sys.argv) != 4 or sys.argv[3] not in A.ROLES:
    sys.exit(__doc__)
uid, name, role = sys.argv[1].strip().lower(), sys.argv[2], sys.argv[3]
pin = getpass.getpass('PIN (at least 4 digits): ')
if len(pin) < 4 or getpass.getpass('Again: ') != pin:
    sys.exit('PINs did not match or are too short.')
rows = A.users(); salt = secrets.token_hex(8)
rows[uid] = {'id': uid, 'name': name, 'role': role, 'salt': salt, 'pin_hash': A.hash_pin(pin, salt)}
with open(A.USERS, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f); w.writerow(['id', 'name', 'role', 'salt', 'pin_hash'])
    for r in rows.values():
        w.writerow([r['id'], r['name'], r['role'], r['salt'], r['pin_hash']])
os.chmod(A.USERS, 0o600)
print(f'Saved {uid} ({role}).')
