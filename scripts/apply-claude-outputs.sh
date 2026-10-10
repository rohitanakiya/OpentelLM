#!/usr/bin/env bash
# Move files Claude delivered into "Claude outputs/" to where they belong.
#
# Claude can't write to some protected paths (like .github/workflows/), so it
# sends those files as downloads, which land in "Claude outputs/". Each file
# there is moved over the one tracked file in the repo with the same name.
# Files with no match or more than one match are left alone and reported.
#
# Usage (from anywhere inside the repo):  bash scripts/apply-claude-outputs.sh
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"
inbox="Claude outputs"

if [ ! -d "$inbox" ]; then
  echo "Nothing to apply: no \"$inbox\" folder."
  exit 0
fi

moved=0
left=0
while IFS= read -r -d '' file; do
  name="$(basename "$file")"
  # Ignore copies inside the inbox itself, in case it was committed earlier.
  mapfile -t matches < <(git ls-files | grep -v "^$inbox/" | awk -v n="$name" -F/ '$NF == n')
  if [ "${#matches[@]}" -eq 1 ]; then
    mv -f "$file" "${matches[0]}"
    echo "  moved  $name  ->  ${matches[0]}"
    moved=$((moved + 1))
  elif [ "${#matches[@]}" -eq 0 ]; then
    echo "  LEFT   $name  (no tracked file with this name; move it by hand)"
    left=$((left + 1))
  else
    echo "  LEFT   $name  (several tracked files share this name: ${matches[*]})"
    left=$((left + 1))
  fi
done < <(find "$inbox" -type f -print0)

# Remove the folder once it's empty.
find "$inbox" -depth -type d -empty -delete 2>/dev/null || true

echo "Applied $moved file(s); $left left in \"$inbox\"."
[ "$left" -eq 0 ]
