#!/bin/bash
set -e

ROOT_DIR="$(git rev-parse --show-toplevel)"
cd "$ROOT_DIR"

AUTO_CONFIRM=0
BRANCH=""
for arg in "$@"; do
    if [[ "$arg" == "--yes" || "$arg" == "-y" ]]; then
        AUTO_CONFIRM=1
    elif [[ "$arg" != -* ]]; then
        BRANCH="$arg"
    fi
done

if [ -z "$BRANCH" ]; then
    CURRENT_BRANCH="$(git branch --show-current)"
    if [ "$CURRENT_BRANCH" != "main" ] && [ -n "$CURRENT_BRANCH" ]; then
        BRANCH="$CURRENT_BRANCH"
    else
        echo "Usage: $0 [--yes] <branch>   (or run it from the branch to merge)"
        exit 1
    fi
fi

echo "=========================================================="
echo "Comparing branch '$BRANCH' with 'main'..."
echo "=========================================================="

# 1. Check working tree cleanliness
if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: Working tree has unstaged or uncommitted changes."
    echo "Please commit or stash your changes before proceeding."
    git status --short
    exit 1
fi

# 2. Fetch remote state
echo "Fetching latest branches from remote origin (main, $BRANCH)..."
git fetch origin main "$BRANCH" 2>/dev/null || git fetch origin main

# 3. Check for new commits
echo ""
echo "--- New commits in '$BRANCH' not in 'main' ---"
COMMITS_DIFF="$(git log --oneline --graph origin/main.."$BRANCH")"
if [ -z "$COMMITS_DIFF" ]; then
    echo "Branches '$BRANCH' and 'main' are up to date; no new commits to merge."
    exit 0
fi
echo "$COMMITS_DIFF"
echo ""

# 4. Display Diff Stat
echo "--- Files Changed Summary (Diff Stat) ---"
git diff --stat origin/main.."$BRANCH"
echo ""

# 5. Dry-run conflict check and detailed report
echo "Checking for potential merge conflicts..."
set +e
CONFLICT_OUTPUT=$(git merge-tree --write-tree origin/main "$BRANCH" 2>&1)
CONFLICT_EXIT=$?
set -e

if [ $CONFLICT_EXIT -ne 0 ]; then
    echo ""
    echo "=========================================================="
    echo "MERGE CONFLICTS REPORT"
    echo "=========================================================="
    echo "CRITICAL: Cannot auto-merge due to conflicts between '$BRANCH' and 'main'."
    echo ""
    echo "Conflicting files and details:"
    echo "$CONFLICT_OUTPUT" | grep -E "(CONFLICT|Auto-merging)" || echo "$CONFLICT_OUTPUT"
    echo ""
    echo "Resolution steps on branch '$BRANCH':"
    echo "  1. Switch to branch '$BRANCH':        git checkout $BRANCH"
    echo "  2. Merge latest remote main:       git merge origin/main"
    echo "  3. Open conflicting files and resolve between <<<<<<< and >>>>>>>"
    echo "  4. Run tests to verify build integrity."
    echo "  5. Commit resolution:              git commit -m 'fix: resolve merge conflicts with main'"
    echo "  6. Push resolved code to $BRANCH:     git push origin $BRANCH"
    echo "  7. Re-run this script to merge:    bash scripts/compare_and_merge_main.sh $BRANCH"
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
git merge "$BRANCH" -m "merge: integrate branch $BRANCH into main after diff comparison and user verification"

echo "Pushing merged changes to origin/main..."
git push origin main

rm -f "$APPROVAL_TOKEN"

echo "Switching back to working branch '$BRANCH'..."
git checkout "$BRANCH"

echo ""
echo "=========================================================="
echo "SUCCESS: Branch '$BRANCH' merged into 'main' and pushed to remote."
echo "=========================================================="
