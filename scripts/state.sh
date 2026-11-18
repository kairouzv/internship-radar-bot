#!/usr/bin/env bash
# Persistance de l'état (base SQLite) sur une branche Git « state » : gratuit, sans serveur.
# Usage : scripts/state.sh restore | save      (variables : GITHUB_TOKEN, GITHUB_REPOSITORY)
set -euo pipefail
remote="${STATE_REMOTE:-https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"

case "${1:-}" in
  restore)
    rm -rf state
    git clone --quiet --depth 1 --branch state "$remote" state 2>/dev/null || mkdir -p state
    ;;
  save)
    cd state
    # Un seul commit racine, repoussé en force : l'historique ne grossit jamais.
    rm -rf .git
    git init -q -b state
    git remote add origin "$remote"
    git config user.name "radar-bot"
    git config user.email "radar-bot@users.noreply.github.com"
    git add -A
    git commit -q -m "state $(date -u +%FT%TZ)" --allow-empty
    git -c push.negotiate=false push -q --force origin HEAD:state
    ;;
  *) echo "usage: $0 restore|save" >&2; exit 2 ;;
esac
