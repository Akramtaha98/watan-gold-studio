# WaRiSAN Gold Studio interface

Operate mode; retain native Streamlit controls and the established white/neutral/teal system. Dark text #183135, primary #126b73, secondary surface #f3f6f5, native sans. Compact 2rem title; restrained 1.7rem tabular metric values. No decorative graphics or animation.

Full Pipeline Mode sidebar settings: API Base URL, Served Model ID, Gold Version Tag and Reviewer Role. Personal staff names are not collected. Advanced settings and policy stay collapsed; a compact Test Connection row carries Connected/Offline status. Header contains one status bar, four primary metrics and four provisional Gold counts. Detailed provenance/quality data stays collapsed.

A sequential stepper follows action order: 1. Upload & Verify; 2. PTSL Human Review; 3. Validate & Export. Review uses stage pills, two 320px scrollable comparison containers, plain-text masked content and three distinct decisions. Editing is an explicit form; Excel exchange and bulk actions use progressive disclosure. Release uses one deferred validated ZIP download, final counts and collapsed individual artifacts/audit. Native keyboard focus, tooltips, semantic headings, responsive columns and offline/disabled/error states remain intact.

Dedicated evaluation is the default. Its optional grouped SFT holdout toggle is off and the 12% slider disabled until enabled. Optional ShareGPT and RAG settings remain collapsed. Full canonical Parquet and lean JSONL are distinct release layers.

Only one step renders at a time. Completed steps remain reachable; unavailable future steps are disabled. The progress indicator reflects verified intake, a fully resolved global PTSL queue, and a validated release. Retryable verification errors stay visible in Step 1. Review completion uses the global queue rather than the stage filter.

Workspace Mode is the first sidebar control, followed by Reviewer Role. Linguist defaults to reviewer-only mode, unless the user explicitly selects another view. The focused workspace omits the stepper, connection settings, uploader, full-pipeline metrics and export controls. It retains three focused counters (remaining, reviewed, role), comparison, decisions and Excel. Empty/unverified and completed states are distinct. Final completion is a handoff card; full mode resumes Step 3. Technical settings live independently of widgets so hidden controls cannot reset the verification profile.

Persistence upgrade: Saved batch and Release History live in the sidebar. Analytics stays collapsed on Steps 1/3 and contains aggregate native charts only. Step 2's External PTSL Portal Sync card shows progress and refresh/poll controls; it does not replace the focused review decisions. Clean JSONL downloads are default; governance is an explicit option. Local production mode gates the shared curator workspace before loading data. History uses verified immutable snapshots and does not mutate the active batch.
