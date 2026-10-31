#!/bin/sh
# Lists folders and zips that look like old copies of this project so you can decide what to delete yourself.
# It only lists; it never deletes anything.   sh scripts/list_project_copies.sh
for d in "$HOME/Documents" "$HOME/Downloads" "$HOME/Desktop"; do
  find "$d" -maxdepth 3 \( -iname "*verifai*" -o -iname "*verif-ai*" \) -not -path "*/node_modules/*" 2>/dev/null
done | while read -r p; do
  size=$(du -sh "$p" 2>/dev/null | cut -f1)
  printf '%-8s %s\n' "$size" "$p"
done
echo
echo "The live project is: $(cd "$(dirname "$0")/.." && pwd)"
