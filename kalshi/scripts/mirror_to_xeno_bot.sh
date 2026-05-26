#!/usr/bin/env bash
# Mirror this repo to github.com/SniperVRT/Xeno-Bot.
#
# Prereqs:
#   1. `GITHUB_TOKEN` env var with repo-create scope (classic PAT or fine-
#      grained token with "Administration: write" on the SniperVRT org).
#   2. Run from anywhere inside this repository (it `cd`s to the toplevel).
#
# What it does:
#   - Creates SniperVRT/Xeno-Bot if it doesn't exist (private by default;
#      pass `--public` to make it public).
#   - Adds a `xeno` remote pointing at the new repo.
#   - Pushes every branch and every tag with --mirror semantics.
#
# Usage:
#   GITHUB_TOKEN=ghp_xxx ./scripts/mirror_to_xeno_bot.sh
#   GITHUB_TOKEN=ghp_xxx ./scripts/mirror_to_xeno_bot.sh --public

set -euo pipefail

OWNER="SniperVRT"
REPO="Xeno-Bot"
VISIBILITY="private"
if [[ "${1:-}" == "--public" ]]; then
  VISIBILITY="public"
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "ERROR: set GITHUB_TOKEN first (PAT with repo create scope)." >&2
  exit 1
fi

cd "$(git rev-parse --show-toplevel)"

# 1. Create the repo (idempotent: 422 = already exists).
echo "Creating ${OWNER}/${REPO} (${VISIBILITY}) ..."
http_status=$(curl -sS -o /tmp/xeno_create.json -w "%{http_code}" \
  -X POST "https://api.github.com/orgs/${OWNER}/repos" \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -d "{\"name\":\"${REPO}\",\"private\":$( [[ $VISIBILITY == "private" ]] && echo true || echo false )}" \
  || true)

case "${http_status}" in
  201) echo "  created.";;
  422) echo "  already exists — continuing.";;
  *)
    echo "  unexpected status ${http_status}:"
    cat /tmp/xeno_create.json
    # Fall back to user-scoped endpoint in case OWNER is a user, not an org.
    echo "  retrying as user repo ..."
    curl -sS -o /tmp/xeno_create.json -w "%{http_code}\n" \
      -X POST "https://api.github.com/user/repos" \
      -H "Authorization: Bearer ${GITHUB_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      -d "{\"name\":\"${REPO}\",\"private\":$( [[ $VISIBILITY == "private" ]] && echo true || echo false )}" || true
    ;;
esac

# 2. Add / refresh the remote.
remote_url="https://${GITHUB_TOKEN}@github.com/${OWNER}/${REPO}.git"
if git remote get-url xeno >/dev/null 2>&1; then
  git remote set-url xeno "${remote_url}"
else
  git remote add xeno "${remote_url}"
fi

# 3. Push everything.
echo "Pushing branches ..."
git push xeno --all
echo "Pushing tags ..."
git push xeno --tags || true

# 4. Strip the token from the stored URL so it doesn't sit in .git/config.
git remote set-url xeno "https://github.com/${OWNER}/${REPO}.git"

echo
echo "Done. https://github.com/${OWNER}/${REPO}"
