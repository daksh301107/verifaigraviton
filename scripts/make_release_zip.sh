#!/bin/sh
# Builds a zip of the project that is safe to share: no API keys (.env), no saved records, no signing key, no local
# databases or caches. Run from anywhere:  sh scripts/make_release_zip.sh
set -e
cd "$(dirname "$0")/.."
OUT="${1:-$HOME/Desktop/verifai-release-$(date +%Y%m%d).zip}"
rm -f "$OUT"
zip -r "$OUT" . \
  -x ".env" ".env.*" "records/*" "records" "__pycache__/*" "*/__pycache__/*" "*.pyc" ".DS_Store" "*/.DS_Store" \
     "reversal_cost_engine/demo.db" "*.db" "*.log" "logs/*" "logs" "node_modules/*" ".git/*" ".claude/*" \
     "policy_data/users.csv" > /dev/null
echo "Wrote $OUT"
echo "Left out: .env (keys), records/ (saved decisions), caches and databases, policy_data/users.csv (real PIN hashes; the zip carries users.sample.csv with the documented demo users instead)."
echo "Check:  unzip -l \"$OUT\" | grep -E '\\.env|records/|users.csv' || echo 'clean'"
