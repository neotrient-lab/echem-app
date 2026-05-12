#!/bin/bash
# ============================================================
# Neotrient Echem App v0.2.0 — Push an update to GitHub
#
# DOUBLE-CLICK this file in Finder whenever you've changed the code
# in alpha_2/ and want your team to see the new version.
#
# It will:
#   1. Show you what changed since last push
#   2. Stage and commit everything
#   3. Push to GitHub (neotrient-lab/echem-app)
# ============================================================

set -e

PROJECT="$(cd "$(dirname "$0")"; pwd)"

clear
echo ""
echo "============================================================"
echo "   Neotrient Echem App v0.2.0 — Push update to GitHub"
echo "============================================================"
echo ""

cd "$PROJECT"

if [ ! -d ".git" ]; then
    echo "  ERROR: $PROJECT is not a git repo yet."
    echo "  Initialize once with:  git init && git remote add origin"
    echo "    https://github.com/neotrient-lab/echem-app.git"
    read -p "Press Return to close..."
    exit 1
fi

echo "Repo:       $(git remote get-url origin 2>/dev/null || echo '(no remote)')"
echo "Branch:     $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '(no branch)')"
echo ""
echo "What changed since the last push:"
echo "-----------------------------------------"
git status --short
echo "-----------------------------------------"
echo ""

if [ -z "$(git status --short)" ]; then
    echo "  Nothing to push — working tree is clean."
    read -p "Press Return to close..."
    exit 0
fi

# Safety check: confirm secrets aren't in the staging area
git add -A
LEAKS=$(git diff --cached --name-only | grep -E "PASSWORDS\.txt|\.venv/|/exports/|/data/sessions/" || true)
if [ -n "$LEAKS" ]; then
    echo ""
    echo "  WARNING: the following look like they should NOT be uploaded:"
    echo "$LEAKS"
    echo ""
    read -p "  Continue anyway? (y/N): " ANSWER
    if [ "$ANSWER" != "y" ] && [ "$ANSWER" != "Y" ]; then
        git reset >/dev/null
        echo "  Aborted (changes un-staged).  Adjust .gitignore and try again."
        read -p "Press Return to close..."
        exit 1
    fi
fi

read -p "Short description of this update (e.g. 'SWV preset fix'): " MSG
if [ -z "$MSG" ]; then
    MSG="Update"
fi

git commit -m "$MSG"
echo ""
echo "Pushing to GitHub..."
git push

echo ""
echo "============================================================"
echo "   Done!  Your team can pull the new version from:"
echo "   $(git remote get-url origin)"
echo "============================================================"
echo ""
read -p "Press Return to close..."
