import subprocess, os, sys

base = os.path.dirname(os.path.abspath(__file__))
os.chdir(base)
out_path = os.path.join(base, "_git_output.log")

cmds = [
    ("1. git status --porcelain", ["git", "status", "--porcelain"]),
    ("2. git diff --name-only HEAD", ["git", "diff", "--name-only", "HEAD"]),
    ("3. git add -A", ["git", "add", "-A"]),
    ("4. git status --short", ["git", "status", "--short"]),
    ("5. git commit", ["git", "commit", "-m", "Fix provider validation: PROV_CALLERID_* is now OPTIONAL (only PROV_ENDPOINT_* required); Asterisk trunk default CLI used when unset", "--allow-empty-message", "--allow-empty"]),
    ("6. git push origin main", ["git", "push", "origin", "main"]),
]

with open(out_path, "w", encoding="utf-8") as f:
    for label, args in cmds:
        f.write("=== " + label + " ===\n")
        r = subprocess.run(args, capture_output=True, text=True)
        if r.stdout:
            f.write(r.stdout)
            if not r.stdout.endswith("\n"):
                f.write("\n")
        if r.stderr:
            f.write(r.stderr)
            if not r.stderr.endswith("\n"):
                f.write("\n")
        f.write("[exit " + str(r.returncode) + "]\n\n")
    f.write("DONE\n")

print("Output written to", out_path)
