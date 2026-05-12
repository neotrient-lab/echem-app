# Changelog

## v0.2.0 — 2026-05-08

First release of the alpha_2 line.  Branched from v0.1.15.

### Added

- **SWV technique support.**  Square-wave voltammetry alongside CV.
  - New `SWVParameters` dataclass with `e_dep` / `t_dep` (deposition),
    `e_amplitude`, `frequency_hz`, `do_reverse_sweep`,
    `acquisition_frac_autoadjust`, plus a float `max_bandwidth_hz`.
  - `build_swv_script()` emits the deposition step + equilibration +
    SWV sweep in PSTrace-compatible MethodSCRIPT.
  - Live voltammogram switches to three-trace mode (I forward / I
    reverse / I difference) when running SWV.
  - New `preset-swv-1` matches PSTrace's auto-pick chip configuration
    for SWV byte-for-byte (pgstat_mode 3, bandwidth 292.527 Hz,
    autorange 100 nA – 1 mA, deposition at -1.0 V for 60 s).
- **Sequence / batch run mode.**  Plan multiple samples up front.
  - New "Create Sequence" phase between Configuration and Sample
    Identification.  Multiple batches per session; each batch can
    use a different preset (CV vs SWV, manual vs drop-detect).
  - Sequence overview card on Phase 1 (Sample Identification) shows
    the full plan with the current row highlighted, adaptive columns
    by sample-identification mode (Clinical → Donor/Collection/Site/
    Aliquot; Standard → Type/Conc./Date/Lot/Solvent).
  - `Run next sample` button auto-advances through the sequence.
    On the last sample the label becomes `End sequence` and prompts
    for confirmation before jumping to Finalize.
  - Cross-batch preset switch — when the next sample belongs to a
    different batch with a different preset, the session preset is
    re-issued to the server so the chip runs the right script.
- **Two sample-identification form variants.**
  - *Clinical sample* — Sample ID, Donor pouch, Collection date,
    Storage location, Aliquot purpose, Clinical site, Notes.
  - *Standard solution* — Sample ID, Sample type (Standard / Blank /
    QC / Sample), Concentration value+unit, Prep date (defaults to
    today), Lot / batch, Solvent / matrix, Notes.
  - Picked once in Configuration → Sample identification → Sample
    category.  Applied to Manual entry, Phase 1 confirm card, batch
    tables, sequence overview, and Analysis "Samples to analyse"
    table.
- **Self-describing CSV output.**  Every measurement CSV starts with
  a `# key=value` metadata header (experiment, operator, device,
  sample, every preset parameter, status, notes).  The JSON sidecar
  is kept for backwards compatibility.
- **Protected presets.**  `preset-1`, `preset-swv-1`, and
  `Dummy cell test` are flagged `protected: true`.  Deleting them
  requires the admin password (`NEO-001`).  Editing does not.
- **`Finalize` phase** — renamed from "Save Data" since data is
  already auto-saved on each measurement.  Phase 5 is now about
  sign-off + export bundle.
- **Header pills** — Operator pill on Configuration / Create Sequence;
  Sample pill on Phase 1–3; Batch pill on Phase 1–3 in Sequence mode.
- **Measurement lock-down** — during a running measurement, sidebar
  navigation is blocked, the header `Cancel session` button is
  disabled, and a browser `beforeunload` prompt warns before tab close.
- **Cancel session = full reset** — wipes every piece of in-memory
  state, every Configuration form field, every Sequence-mode artefact,
  and re-fetches presets / devices / models.  The app behaves as if
  it just launched.
- **Workflow progress reset** — `resetPhaseProgress()` clears the
  "done" pills for downstream phases when the operator loops back
  (next sample, re-measure, cancel measurement).
- **Real-time refresh helpers** — `refreshPresets()` /
  `refreshDevices()` / `refreshModels()` keep ALL dropdowns
  (Configuration + Sequence Build) in sync after any CRUD operation.
  Setting a preset as default also re-issues `/api/session/start` so
  the server picks up the new active preset immediately.

### Changed

- **"Hospital site" → "Clinical site"** throughout the UI and JSON.
  Old data with `hospital_site` keys still loads via back-compat
  fallbacks.
- **Sidebar workflow labels** —
  - Phase 1 subtext: "Operator · Device · Preset" → "Operator · Device"
    (Preset is contextual: in Sequence mode it moves to Create
    Sequence).
  - Phase 3 subtext: "QR scan · LIMS lookup" → "Identify and confirm".
  - Phase 5 title: "Save Data" → "Finalize".
  - Phase 5 measurement subtitle: "Cyclic voltammetry" → "Voltammetry"
    (technique-agnostic).
- **Button wording cleanup** — removed arrows / plus signs from text
  buttons for a more formal medical-device look:
  - `Continue to analysis →` → `Run analysis`
  - `Proceed to save →` → `Finalize`
  - `+ Create batch` → `Create batch`
  - `+ Add sample` → `Add sample`
  - `← Back to Configuration` → `Back to Configuration`
  - `← Back` → `Back`
- **Sidebar workflow numbering** — CSS counter so numbers stay
  correct in both Single mode (6 phases) and Sequence mode (7
  phases).  Numbers update automatically when the Sequence Build
  phase is shown/hidden.
- **Configuration page reorder** — Run mode pinned to the top so
  toggling it doesn't reflow the rest.  Sample category second.
  Operator / Device / Preset stack below.

### Fixed

- **SWV measurement crashed with `'SWVParameters' object has no
  attribute 'n_scans'`.**  Cycle-index calculation in iter_measurement
  now uses `getattr(opts.params, 'n_scans', 1)` and only fires for CV
  (SWV has no scans).
- **Live voltammogram stayed empty during SWV.**  `resetChart()` was
  wiping `chartScanIndex` AFTER `initLiveChart()` populated it.
  `resetChart()` now only resets the progress-bar UI; chartData /
  chartScanIndex are owned by `initLiveChart()`.
- **Setting a preset as default did not switch the active session.**
  `/api/session/start` is now re-issued after `setDefaultPreset` so
  the server's `_STATE.preset` updates without a full Begin-session
  restart.
- **QR scanner disappeared on Phase 1 after `Add sample` in Sequence
  Build.**  Stale `confirmCard.classList.add('hidden')` call removed.
- **Workflow bar showed phases 4-7 as green after looping back to
  Sample Identification for the next sample.**  `state.maxPhaseReached`
  is now reset (not just monotonically increased) on next-sample /
  re-measure / cancel-measurement.
- **Manage presets — deleted preset still appeared in dropdowns.**
  After any CRUD operation, all preset dropdowns (Configuration's
  `fPreset`, Sequence Build's `seqNewPreset`) re-render.
- **Sequence per-sample save copied previous sample's meta.**  Stale
  `state.sample` cleared on `seqEditSample`.  Manual-entry modal in
  Sequence mode uses the form values seqEditSample pre-filled rather
  than re-pre-filling from `state.sample`.

### Architecture

- The Sequence builder is integrated into the main SPA (`index.html`)
  as a phase, not a standalone `/sequence` page.  Same sidebar, same
  header, same button styles as Single mode.  The old `/sequence`
  URL redirects to `/` for back-compat.
- `cv_app/` is now a CV+SWV library (CV-specific name retained for
  back-compat; renamed in a future major release).
- New refresh helper functions reduce duplicate `loadPresets() +
  populatePhase0() + ...` patterns across the codebase.

---

## v0.1.15 (and earlier alpha_1 versions)

See `echem_app/__init__.py` in the alpha_1 folder for the full
v0.1.0 – v0.1.15 history.

Key alpha_1 deliverables:
- First in-team alpha release (v0.1.0).
- PSTrace-compatible CV preset (`preset-1`) matching auto-pick chip
  configuration byte-for-byte.
- Drop-detect via voltage threshold on OCP baseline.
- Mobile UI (iPhone / iPad) with bottom Prev / Next navigation.
- BLE transport for PS-539B.
- Auto-saved CSV + JSON sidecar.
- AI inference for concentration prediction.
