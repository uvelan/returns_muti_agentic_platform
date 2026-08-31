# ACC phase 3 — category B audit by fault injection

**Base sha: `63744f2abb1ff186617dee0c7d541fd6f4870db2`** — `63744f2a docs(merge): ACC-2 merged -- RV PASS after three rounds`.
Branch `feat/acc-audit-b` cut from that commit.

## step:00 — base verification, and a correction to the dispatch

The dispatch said the base "must be the current tip of `master` (should be around
`63744f2a docs(merge): ACC-2 merged`)". Those two identifiers name different
commits, so the check was run rather than assumed:

```
$ git log --oneline -1 master
0448d32a feat: refine conversational returns experience
$ git rev-parse HEAD
63744f2abb1ff186617dee0c7d541fd6f4870db2
$ git branch --show-current
refactor/unified-return-platform
```

`63744f2a` is **not** on `master`:

```
$ git merge-base --is-ancestor 63744f2a master  -> NO
$ git merge-base --is-ancestor 0448d32a HEAD    -> YES
$ git branch -a --contains 63744f2a
* refactor/unified-return-platform
$ git rev-list --count 0448d32a..63744f2a
872
```

So the integration branch is `refactor/unified-return-platform`, and `master` is
**872 commits behind it** — it omits every merged slice, which is precisely the
trap the dispatch warns about ("branched from an ancestor that compiled, passed,
and silently omitted every merged slice"). Had the ref label been followed instead
of the sha, this run would have been the ninth.

**Resolution:** the sha is the load-bearing identifier and it matches HEAD exactly,
including its subject line. Branched from `63744f2a`. The dispatch's *"tip of
master"* wording is wrong and is recorded here rather than quietly worked around.

```
$ git checkout -b feat/acc-audit-b 63744f2a
Switched to a new branch 'feat/acc-audit-b'
$ git rev-parse HEAD
63744f2abb1ff186617dee0c7d541fd6f4870db2
$ git status --porcelain
(empty)
```
