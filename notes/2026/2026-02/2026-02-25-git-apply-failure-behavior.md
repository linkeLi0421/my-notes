---
id: 2026-02-25-git-apply-failure-behavior-65e42d50
date: 2026-02-25
project: git
topic: "apply failure behavior"
tags: [git, patch, diff, apply]
source: chat
confidence: n/a
---

# Summary note (git apply failure behavior)

## Summary
1. git apply can relocate a hunk when original line/context no longer matches but another matching context exists later; line numbers are not strict anchors.
2. In this case, a hunk intended for early cram_io.c matched a later repeated context and applied with a large offset.
3. Malformed unified diff content (for example a raw empty/body line without diff prefix) can be effectively treated as trailing garbage; part of the hunk may still apply, which makes location/debugging harder.
4. Your recent fix direction is correct: enforce strict reverse apply checks and reject offset/fuzz outcomes, and sanitize generated hunk body lines before apply.

## Reproducible code
```bash
#!/usr/bin/env bash
set -euo pipefail

workdir="$(mktemp -d)"
echo "WORKDIR=$workdir"
cd "$workdir"
git init -q

echo "== Case 1: Hunk relocates to wrong repeated context =="
cat > demo.txt <<'TXT'
START
alpha
beta
gamma
delta
epsilon
zeta
END1

alpha
beta
gamma
delta
epsilon
zeta
END2
TXT

git add demo.txt
git commit -q -m init

# Build patch targeting first block
perl -0777 -i -pe 's/gamma\n/gamma\nINJECTED\n/' demo.txt
git diff > insert.patch

# Break first block only; second block still matches
git checkout -- demo.txt
perl -0777 -i -pe 's/delta\n/delta_changed\n/' demo.txt

git apply --check insert.patch
git apply --verbose insert.patch
nl -ba demo.txt | sed -n '1,30p'

echo
echo "== Case 2: malformed trailing diff text still passes git apply check =="
printf 'one\ntwo\nthree\n' > a.txt
git add a.txt
git commit -q -m add_a

cat > broken.patch <<'PATCH'
diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,3 +1,4 @@
 one
+X
 two
 three
BROKEN_TRAILING_LINE
PATCH

git apply --check broken.patch
patch -p1 --dry-run --verbose < broken.patch || true
git apply broken.patch
nl -ba a.txt
```

## Expected key output
- Case 1: Hunk #1 succeeded at ... (offset ... lines) and INJECTED appears in second block.
- Case 2: patch reports Ignoring the trailing garbage, while git apply --check still passes.
