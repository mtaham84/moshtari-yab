#!/bin/bash
set -e

ROOT_DIR="$(git rev-parse --show-toplevel)"
cd "$ROOT_DIR"

AUTO_CONFIRM=0
if [[ "$1" == "--yes" || "$1" == "-y" ]]; then
    AUTO_CONFIRM=1
fi

echo "=========================================================="
echo "بررسی و مقایسه تغییرات شاخه taha با شاخه main"
echo "=========================================================="

# 1. Check working tree cleanliness
if [ -n "$(git status --porcelain)" ]; then
    echo "خطا: ورکینگ‌تری دارای تغییرات ذخیره‌نشده یا کامیت‌نشده است."
    echo "لطفاً ابتدا تغییرات شاخه فعلی را کامیت یا stash نمایید."
    git status --short
    exit 1
fi

# 2. Fetch remote state
echo "در حال دریافت آخرین وضعیت شاخه‌ها از گیت‌هاب..."
git fetch origin main taha

# 3. Check for new commits
echo ""
echo "--- لیست کامیت‌های جدید در شاخه taha نسبت به main ---"
COMMITS_DIFF="$(git log --oneline --graph origin/main..taha)"
if [ -z "$COMMITS_DIFF" ]; then
    echo "شاخه taha و main همگام هستند؛ هیچ کامیت جدیدی برای ادغام وجود ندارد."
    exit 0
fi
echo "$COMMITS_DIFF"
echo ""

# 4. Display Diff Stat
echo "--- خلاصه تغییرات فایل‌ها (Diff Stat) ---"
git diff --stat origin/main..taha
echo ""

# 5. Dry-run conflict check
echo "در حال بررسی و اعتبارسنجی تعارضات (Conflict Check)..."
if ! git merge-tree --write-tree origin/main taha > /dev/null 2>&1; then
    echo "خطای بحرانی: بین شاخه taha و main تعارض (Merge Conflict) وجود دارد!"
    echo "ادغام متوقف شد. تعارضات باید در شاخه taha با دستور git merge origin/main رفع گردند."
    exit 1
fi
echo "وضعیت تعارضات: هیچ تعارضی وجود ندارد (آماده ادغام امن)."
echo ""

# 6. User confirmation
if [ "$AUTO_CONFIRM" -ne 1 ]; then
    echo "=========================================================="
    read -r -p "آیا تغییرات فوق را بررسی و ادغام در main را تأیید می‌کنید؟ (y/N): " CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" && "$CONFIRM" != "بله" ]]; then
        echo "عملیات ادغام توسط کاربر لغو شد."
        exit 0
    fi
fi

# 7. Execute merge and push with authorization token
APPROVAL_TOKEN=".git/SAFE_MERGE_APPROVED"
touch "$APPROVAL_TOKEN"
export ALLOW_MAIN_COMMIT=1
export ALLOW_MAIN_PUSH=1

echo ""
echo "در حال سوییچ به شاخه main و اعمال ادغام..."
git checkout main
git pull origin main
git merge taha -m "merge: integrate branch taha into main after diff comparison and user verification"

echo "در حال ارسال تغییرات ادغام‌شده به origin/main..."
git push origin main

rm -f "$APPROVAL_TOKEN"

echo "بازگشت به شاخه کاری taha..."
git checkout taha

echo ""
echo "=========================================================="
echo "ادغام شاخه taha در main با موفقیت انجام و به گیت‌هاب ارسال شد."
echo "=========================================================="
