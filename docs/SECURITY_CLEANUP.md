# Proxy config history cleanup

xray/new_configs.txt was committed before it was ignored. Removing it from the current index does not erase earlier commits. Rotate every proxy link that was ever committed, then coordinate a fresh clone for everyone using this repository.

Run the history rewrite from a mirror clone; these commands rewrite all refs and force-push them:

    git clone --mirror <repository-url> moshtari-yab.git
    cd moshtari-yab.git
    git filter-repo --path xray/new_configs.txt --invert-paths
    git remote add origin <repository-url>
    git push --force --all origin
    git push --force --tags origin

Before the rewrite, check whether any config lines were copied into worktrees or docs. The check prints matching paths only, never the links. It reads the current ignored file or, after removal, its original Git blob. After rewriting history, point it to a private backup because the old Git blob will no longer exist:

    python scripts/check_proxy_leaks.py
    python scripts/check_proxy_leaks.py --source <private-backup-path>

The proxy switcher still reads /root/moshtari-yab/xray/config.json (xray/proxy_switcher.py:26-27); the ignored text file is not its runtime config.
