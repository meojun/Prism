# source snapshot

`prism-research/` is intentionally gitignored by the experiment repository and
is recreated by `bootstrap.sh`. The complete source working-tree delta is
therefore stored in `prism_research_worktree.patch`.

Base source repository commit:

```text
595ec1f170e75a43897a7a2ad58ac5a9820aa2e8
```

Apply from a clean checkout of that commit:

```bash
git apply /path/to/Prism/patches/final_baseline_ready/prism_research_worktree.patch
```

Files in the patch: 36
Snapshot taken: 2026-08-23T13:39:38Z
