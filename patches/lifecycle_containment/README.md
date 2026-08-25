# Runtime freeze — migration-lifecycle failure containment

The serving runtime lives in `prism-research/`, which is a separate checkout and
is gitignored here. Its identity is therefore recorded as a base commit plus the
complete working-tree delta, exactly as every previous freeze in this project.

    base commit                 595ec1f170e75a43897a7a2ad58ac5a9820aa2e8
    worktree patch sha256       49f47ebd7c75aecdac2d67efea1b3d121b9e599693df514cfd71bfe10724881d
    RUNTIME_SOURCE_TREE_HASH    7fbd431c6a636df0c72bb6a40324f851d002d204

`RUNTIME_SOURCE_TREE_HASH` is a real `git write-tree` hash of the live runtime,
computed through a throwaway index so the checkout's own index is untouched.

Rebuild with:

    git -C prism-research reset --hard 595ec1f170e75a43897a7a2ad58ac5a9820aa2e8
    git -C prism-research apply patches/lifecycle_containment/prism_research_worktree.patch

Then verify by re-snapshotting and comparing the patch sha256.

Contents of this freeze relative to the estimator-corrected runtime
(`2b5430c1b04b21e9…`): explicit structured GPU-scheduler shutdown reasons,
fail-closed propagation of unexpected scheduler termination, a bounded
control-path timeout, and a scheduler-endpoint liveness precondition.
Algorithm 1 and Algorithm 2 semantics are unchanged.
