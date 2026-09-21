import subprocess, os, sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

cmds = [
    ("1. git status --porcelain", ["git", "status", "--porcelain"]),
    ("2. git diff --name-only HEAD", ["git", "diff", "--name-only", "HEAD"]),
    ("3. git add -A", ["git", "add", "-A"]),
    ("4. git status --short", ["git", "status", "--short"]),
    ("5. git commit", ["git", "commit", "-m", "Fix provider validation: PROV_CALLERID_* is now OPTIONAL (only PROV_ENDPOINT_* required); Asterisk trunk default CLI used when unset", "--allow-empty-message", "--allow-empty"]),
    ("6. git push origin main", ["git", "push", "origin", "main"]),
]

for label, args in cmds:
    print("=== " + label + " ===")
    sys.stdout.flush()
    r = subprocess.run(args, capture_output=True, text=True)
    if r.stdout:
        sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    print("[exit " + str(r.returncode) + "]")
    print()
    sys.stdout.flush()
