#!/bin/bash
set -e

ROOT_DIR="$(git rev-parse --show-toplevel)"
cd "$ROOT_DIR"

AUTO_CONFIRM=0
if [[ "$1" == "--yes" || "$1" == "-y" ]]; then
    AUTO_CONFIRM=1
fi

echo "=========================================================="
echo "Comparing branch 'taha' with 'main'..."
echo "=========================================================="

# 1. Check working tree cleanliness
if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: Working tree has unstaged or uncommitted changes."
    echo "Please commit or stash your changes before proceeding."
    git status --short
    exit 1
fi

# 2. Fetch remote state
echo "Fetching latest branches from remote origin (main, taha)..."
git fetch origin main taha

# 3. Check for new commits
echo ""
echo "--- New commits in 'taha' not in 'main' ---"
COMMITS_DIFF="$(git log --oneline --graph origin/main..taha)"
if [ -z "$COMMITS_DIFF" ]; then
    echo "Branches 'taha' and 'main' are up to date; no new commits to merge."
    exit 0
fi
echo "$COMMITS_DIFF"
echo ""

# 4. Display Diff Stat
echo "--- Files Changed Summary (Diff Stat) ---"
git diff --stat origin/main..taha
echo ""

# 5. Dry-run conflict check and detailed report
echo "Checking for potential merge conflicts..."
set +e
CONFLICT_OUTPUT=$(git merge-tree --write-tree origin/main taha 2>&1)
CONFLICT_EXIT=$?
set -e

if [ $CONFLICT_EXIT -ne 0 ]; then
    echo ""
    echo "=========================================================="
    echo "MERGE CONFLICTS REPORT"
    echo "=========================================================="
    echo "CRITICAL: Cannot auto-merge due to conflicts between 'taha' and 'main'."
    echo ""
    echo "Conflicting files and details:"
    echo "$CONFLICT_OUTPUT" | grep -E "(CONFLICT|Auto-merging)" || echo "$CONFLICT_OUTPUT"
    echo ""
    echo "Resolution steps on branch 'taha':"
    echo "  1. Switch to branch 'taha':        git checkout taha"
    echo "  2. Merge latest remote main:       git merge origin/main"
    echo "  3. Open conflicting files and resolve between <<<<<<< and >>>>>>>"
    echo "  4. Run tests to verify build integrity."
    echo "  5. Commit resolution:              git commit -m 'fix: resolve merge conflicts with main'"
    echo "  6. Push resolved code to taha:     git push origin taha"
    echo "  7. Re-run this script to merge:    bash scripts/compare_and_merge_main.sh"
    echo "=========================================================="
    exit 1
fi

echo "Conflict status: Clean! No conflicts detected (safe to merge into 'main')."
echo ""

# 6. User confirmation
if [ "$AUTO_CONFIRM" -ne 1 ]; then
    echo "=========================================================="
    read -r -p "Do you approve these changes and want to merge into 'main'? (y/N): " CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        echo "Merge operation cancelled by user."
        exit 0
    fi
fi

# 7. Execute merge and push with authorization token
APPROVAL_TOKEN=".git/SAFE_MERGE_APPROVED"
touch "$APPROVAL_TOKEN"
export ALLOW_MAIN_COMMIT=1
export ALLOW_MAIN_PUSH=1

echo ""
echo "Switching to 'main' branch and applying merge..."
git checkout main
git pull origin main
git merge taha -m "merge: integrate branch taha into main after diff comparison and user verification"

echo "Pushing merged changes to origin/main..."
git push origin main

rm -f "$APPROVAL_TOKEN"

echo "Switching back to working branch 'taha'..."
git checkout taha

echo ""
echo "=========================================================="
echo "SUCCESS: Branch 'taha' merged into 'main' and pushed to remote."
echo "=========================================================="
