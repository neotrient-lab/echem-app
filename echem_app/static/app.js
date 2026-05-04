/* Neotrient Echem App — frontend logic.
 *
 * Phase 0 → Phase 5 controller.  All persistent state (presets / devices /
 * models) is managed by the Flask backend; the browser just lists/edits via
 * REST endpoints.
 */

const state = {
  phase: 0,
  // Highest phase the operator has reached so far.  Sidebar entries above
  // this are locked — operator can only advance via the in-page button on
  // the current phase, but can revisit any prior phase freely.
  maxPhaseReached: 0,
  presets: { items: [], default_id: null },
  devices: { items: [], default_id: null },
  models:  { items: [], default_id: null },
  selectedPresetId: null,
  selectedDeviceId: null,
  selectedModelId: null,
  connectionOpen: false,          // true once /api/connection/test succeeds
  measuredSamples: [],            // list of {sample_id, n_samples, analyzed, ...}
  selectedAnalysisSampleId: null, // active sample on the Analysis page
  inferenceSampleTicks: new Set(),// which samples to feed to the model
  comparisonTicks: new Set(),     // which samples to chart in Overall
  editingPresetId: null,          // when truthy, "Add preset" updates instead of inserts
  editingDeviceId: null,          // ditto for "Add device"
  chartKind: 'bar',               // 'bar' | 'box' | 'swarm'
  selectedIon: null,              // last ion plotted in Overall
  sample: null,
  measureReader: null,    // current SSE reader so we can cancel
  measureInProgress: false,
  measureFinished: false,
  chart: null,
  // For multi-cycle: each scan number gets its own Plotly trace.
  // chartScanIndex maps scan number -> trace index in the figure.
  chartScanIndex: new Map(),
  chartTotalCycles: 1,         // updated from active preset
  chartData: { x: [], y: [], scans: [] },
  predictions: null,
  features: null,
  banner: 'ok',
  qrStream: null,
  qrCanvas: null,
  // Client-side audit log of save events for Phase 5 status display.
  // Each entry: { type: 'saved'|'remeasured'|'analyzed', sample_id, ts }.
  // The server is the source of truth for the data itself; this log just
  // tells the operator what happened in their session.
  eventLog: [],
};

/* Push a sample-level event to the in-session log.  Used by Phase 5 to
 * render an activity stream (Saved / Re-measured / Analyzed). */
function logSampleEvent(type, sample_id, extra) {
  state.eventLog.push({
    type, sample_id,
    ts: new Date().toISOString(),
    ...extra,
  });
}

/* -------------------------------------------------------------------------
 * Boot
 * ----------------------------------------------------------------------- */

document.addEventListener('DOMContentLoaded', async () => {
  // Splash plays first; the rest of the boot work happens in parallel
  // behind it so the operator never has to wait extra time.
  initSplash();
  await Promise.all([loadPresets(), loadDevices(), loadModels()]);
  populatePhase0();
  refreshStatus();
  setInterval(refreshStatus, 5000);
});

/* -------------------------------------------------------------------------
 * Splash video (opening + closing animation)
 * ----------------------------------------------------------------------- */

const SPLASH_MAX_MS = 6000;   // safety cap so the splash never hangs forever

function initSplash() {
  const overlay = document.getElementById('splashOverlay');
  const video   = document.getElementById('splashVideo');
  if (!overlay || !video) return;

  // When the video finishes naturally, fade out the overlay.
  video.addEventListener('ended', hideSplash);
  // Hard timeout in case autoplay is blocked or the file is missing.
  setTimeout(hideSplash, SPLASH_MAX_MS);

  // Try to start playback (some browsers reject autoplay on cold load
  // even when muted; if so, the overlay stays until the safety timeout
  // or the operator clicks Skip).
  const playPromise = video.play();
  if (playPromise && typeof playPromise.catch === 'function') {
    playPromise.catch(err => console.warn('splash video autoplay blocked:', err));
  }
}

function hideSplash() {
  const overlay = document.getElementById('splashOverlay');
  if (!overlay || overlay.classList.contains('hidden')) return;
  overlay.classList.add('fade-out');
  // Remove from DOM flow after the CSS transition completes.
  setTimeout(() => overlay.classList.add('hidden'), 600);
}

/* Replay the splash — used as the "closing" animation when the operator
 * cancels the session. */
function showClosingSplash(durationMs = 2200) {
  const overlay = document.getElementById('splashOverlay');
  const video   = document.getElementById('splashVideo');
  if (!overlay || !video) return;
  overlay.classList.remove('hidden', 'fade-out');
  try {
    video.currentTime = 0;
    const p = video.play();
    if (p && typeof p.catch === 'function') p.catch(() => {});
  } catch (e) { /* ignore */ }
  // Hide again after the requested duration (or when video ends).
  setTimeout(hideSplash, durationMs);
}

/* -------------------------------------------------------------------------
 * Status / sidebar
 * ----------------------------------------------------------------------- */

async function refreshStatus() {
  try {
    const d = await getJSON('/api/status');
    state.lastStatus = d;
    setText('metaOperator', d.operator || '—');
    setText('metaDevice',
      d.device_name ? `${d.device_name} · ${d.device_id}` : '—');
    setText('metaPreset', d.preset_name || '—');
    const ps = document.getElementById('metaPalmsens');
    // Show "Connected" when we actually hold an open link, not just when
    // the device record looks valid.
    const held = !!d.connection_held;
    if (held) {
      ps.textContent = '● Connected';
      ps.title = `${d.palmsens.port || ''}`;
      ps.classList.remove('offline'); ps.classList.add('online');
    } else {
      ps.textContent = '● Disconnected';
      ps.title = d.palmsens.error || '';
      ps.classList.remove('online'); ps.classList.add('offline');
    }
    // Reconcile JS state with the authoritative server status.
    // Without this, a Connect whose response was lost / late / aborted
    // can leave state.connectionOpen=false forever even though the
    // server already holds the connection — and Begin session would
    // refuse to advance because of that stale local flag.
    if (held && !state.connectionOpen) {
      state.connectionOpen = true;
      const connBtn  = document.getElementById('btnConnectDevice');
      const startBtn = document.getElementById('btnStartSession');
      if (connBtn) {
        connBtn.textContent = '✓ Connected';
        connBtn.disabled = true;
      }
      if (startBtn) startBtn.disabled = false;
      // Hide any leftover error banner / connect modal so the operator
      // isn't staring at stale UI.
      document.getElementById('sessionError')?.classList.add('hidden');
      hideConnectModal();
    } else if (!held && state.connectionOpen) {
      // The device unplugged or the server dropped the link — flip back
      // to the disconnected state so the operator must Connect again.
      state.connectionOpen = false;
      const connBtn  = document.getElementById('btnConnectDevice');
      const startBtn = document.getElementById('btnStartSession');
      if (connBtn) {
        connBtn.textContent = 'Connect';
        connBtn.disabled = false;
      }
      if (startBtn) startBtn.disabled = true;
    }
    // Header BT icon — visible only while a BLE connection is held
    const btIcon = document.getElementById('hdrBtIcon');
    if (btIcon) {
      const isBle = held && (d.palmsens.connection_type === 'ble');
      btIcon.classList.toggle('hidden', !isBle);
    }
  } catch (e) {
    console.warn('status fetch failed', e);
  }
}

/* Returns true if PalmSens is connected; otherwise pops the warning modal
 * and returns false.  Always re-fetches /api/status before deciding. */
async function ensurePalmsensConnected() {
  try {
    const d = await getJSON('/api/status');
    state.lastStatus = d;
    if (d.palmsens && d.palmsens.connected) return true;
  } catch (e) { /* fall through to modal */ }
  show('modalNoDevice');
  return false;
}

async function retryPalmsens() {
  const ok = await ensurePalmsensConnected();
  if (ok) { closeModal('modalNoDevice'); toast('PalmSens detected.', 'success'); }
  else { toast('Still not detected.', 'error'); }
  refreshStatus();
}

/* -------------------------------------------------------------------------
 * Phase navigation
 * ----------------------------------------------------------------------- */

function goPhase(n) {
  state.phase = n;
  state.maxPhaseReached = Math.max(state.maxPhaseReached, n);
  for (let i = 0; i <= 5; i++) {
    const el = document.getElementById('screen-' + i);
    if (el) el.classList.toggle('hidden', i !== n);
  }
  // Whenever the visible phase changes, the chart's container width
  // can change too (sidebar collapses/expands on mobile).  Tell Plotly
  // to re-fit so the curve stays proportional.
  if (state.__chartResize) {
    setTimeout(state.__chartResize, 80);
  }
  document.querySelectorAll('.phase').forEach((el, i) => {
    el.classList.remove('active', 'done', 'locked');
    if (i === n) el.classList.add('active');
    else if (i < state.maxPhaseReached) el.classList.add('done');
    if (i > state.maxPhaseReached) el.classList.add('locked');
  });
  // Sample pill: visible only during the chain-of-custody phases (1, 2, 3).
  // Hidden on Phase 0 (no sample yet) and on Phase 4-5 (the operator may be
  // browsing many samples at once).
  document.getElementById('hdrSample').classList.toggle(
    'hidden', n < 1 || n >= 4);

  // Mobile bottom-nav state mirrors the workflow tabs above.
  updateMobileNav(n);

  // Always show the new phase from the top — operators don't expect to land
  // mid-page.  Cover both the .working internal scroller (mobile) and the
  // window scroll position (desktop).
  const work = document.querySelector('.working');
  if (work) work.scrollTop = 0;
  if (typeof window !== 'undefined' && window.scrollTo) {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
  }

  if (n === 1) {
    startQrScanner();
    // If we're returning to Phase 1 after a measurement (or via the
    // sidebar with no fresh sample), don't display the previous sample's
    // confirm card — it's misleading.  Operator must re-scan or use Manual.
    const sid = state.sample && state.sample.sample_id;
    const alreadyMeasured = sid && state.measuredSamples.some(s => s.sample_id === sid);
    if (!sid || alreadyMeasured) {
      const cc = document.getElementById('confirmCard');
      if (cc) cc.classList.add('hidden');
      const cb = document.getElementById('btnConfirmSample');
      if (cb) cb.disabled = true;
      state.sample = null;
      state.scannedQrImage = null;
    } else {
      // We DO have a fresh, not-yet-measured sample (typical case: arriving
      // here from a re-measurement click).  Make sure the confirmCard
      // reflects THIS sample's data, not whatever was rendered last —
      // confirmCard is a static DOM block that won't refresh on its own.
      renderConfirmCard(state.sample, state.sample.__remeasure ? 'MANUAL' : 'MANUAL');
      document.getElementById('btnConfirmSample').disabled = false;
    }
    // Mobile inline form: show whenever there's no confirmed sample yet,
    // hide once confirmCard is up.
    const inl = document.getElementById('inlineManualForm');
    if (inl) {
      const haveSample = !!(state.sample && state.sample.sample_id);
      inl.classList.toggle('hidden', haveSample);
    }
  } else stopQrScanner();
  if (n === 2) preparePhase2();
  if (n === 3) initLiveChart();
  if (n === 4) preparePhase4();
  if (n === 5) preparePhase5();
}

/* Sidebar click handler — only allow re-visiting phases we've reached. */
function userClickPhase(n) {
  if (n > state.maxPhaseReached) return;   // locked
  goPhase(n);
}

/* -------------------------------------------------------------------------
 * Mobile bottom-nav prev/next
 * -----------------------------------------------------------------------
 * On phones the sidebar is hidden and the user navigates with two big
 * buttons at the bottom.  The bottom Next button is the SINGLE primary
 * action: it dispatches per-phase to the same handler the in-page button
 * would have called (Connect → Start session, Confirm sample, Start
 * measurement, Continue to analysis, Proceed to save).  The in-page
 * primary buttons get class "mobile-hide-nav" so they disappear on phones
 * — operators only see the bottom Next.
 */
function prevPhase() {
  if (state.phase > 0) goPhase(state.phase - 1);
}

/* Returns the primary in-page button for the given phase, or null if it
 * isn't currently actionable (disabled or hidden because step's not done). */
function _primaryButtonForPhase(n) {
  switch (n) {
    case 0: return document.getElementById('btnStartSession');
    case 1: return document.getElementById('btnConfirmSample');
    case 2: {
      // Show only when the manual-start row is visible; if drop-detect mode
      // is in flight (#phase2Cancel visible), Next is disabled.
      const row = document.getElementById('phase2Manual');
      if (!row || row.classList.contains('hidden')) return null;
      return document.getElementById('btnStartMeasure');
    }
    case 3: {
      // Available only after measurement finishes (phase3Finished visible).
      const row = document.getElementById('phase3Finished');
      if (!row || row.classList.contains('hidden')) return null;
      return row.querySelector('.btn:not(.btn-secondary):not(.btn-danger)');
    }
    case 4: {
      // Phase 4 has multiple .btn-row elements (one inside the inference
      // model card with "Run analysis", and the trailing one with
      // "Proceed to save").  We want the trailing one — i.e. only direct
      // children of #screen-4, not nested rows inside cards.  Without the
      // ">" combinator querySelector matches "Run analysis" first, so the
      // bottom Next button silently runs analysis again instead of saving.
      return document.querySelector('#screen-4 > .btn-row .btn:not(.btn-secondary):not(.btn-danger)');
    }
    default: return null;
  }
}

function nextPhase() {
  // Phase 5 "Done" → cancel the session (returns to Phase 0 with all
  // session state cleared).  This is the explicit end-of-session action,
  // separate from the in-batch "Run next sample" button that's available
  // on the same screen.
  if (state.phase >= 5) {
    cancelSession();
    return;
  }
  const btn = _primaryButtonForPhase(state.phase);
  if (btn && !btn.disabled) {
    btn.click();
    return;
  }
  // Fallback: pure navigation (e.g. user revisited an old phase via Back).
  const target = state.phase + 1;
  if (target > 5) return;
  if (target > state.maxPhaseReached) return;
  goPhase(target);
}

function _phase3Finished() {
  const row = document.getElementById('phase3Finished');
  return row && !row.classList.contains('hidden');
}

function _nextLabelFor(n) {
  switch (n) {
    case 0: return 'Begin →';
    case 1: return 'Confirm →';
    case 2: return 'Start →';
    case 3: return _phase3Finished() ? 'Analyze →' : 'Measuring…';
    case 4: return 'Save →';
    case 5: return 'Done';
    default: return 'Next →';
  }
}

function updateMobileNav(n) {
  const prev = document.getElementById('mnPrev');
  const next = document.getElementById('mnNext');
  const step = document.getElementById('mnStep');
  if (!prev || !next || !step) return;
  prev.disabled = (n <= 0);
  step.textContent = `Step ${n + 1} / 6`;
  next.textContent = _nextLabelFor(n);
  // Mirror the visible primary button's enabled state.
  const btn = _primaryButtonForPhase(n);
  if (n >= 5) {
    // Phase 5 "Done" — always clickable; runs a new sample workflow.
    next.disabled = false;
  } else if (n === 3 && !_phase3Finished()) {
    // Measurement in progress — never allow advancing, even if a previous
    // run pushed maxPhaseReached past 3.
    next.disabled = true;
  } else if (btn) {
    next.disabled = !!btn.disabled;
  } else {
    // No primary button currently visible — fall back to pure-nav advance
    // only if the next phase is unlocked.
    next.disabled = ((n + 1) > state.maxPhaseReached);
  }
}

/* Poll once a tick so dynamic state changes (button enabled, row unhidden)
 * propagate to the mobile nav without us having to instrument every callsite. */
setInterval(() => {
  if (typeof state !== 'undefined' && document.getElementById('mnNext')) {
    updateMobileNav(state.phase);
  }
}, 400);

/* Backwards-compat stub — analysis tabs were reverted, but any leftover
 * onclick attribute would otherwise throw. */
function setAnalysisTab() { /* no-op since tabs were removed */ }

/* -------------------------------------------------------------------------
 * REGISTRIES — Presets / Devices / Models
 * ----------------------------------------------------------------------- */

async function loadPresets() {
  state.presets = await getJSON('/api/presets');
  state.selectedPresetId = state.presets.default_id;
  renderPresetsList();
}
async function loadDevices() {
  state.devices = await getJSON('/api/devices');
  // Preserve the user's previous selection if it still exists.  Without
  // this, any code path that reloads the device list (set-default,
  // add/delete) silently resets state.selectedDeviceId back to the
  // server's default — which clobbers a USB pick if BLE is the default.
  const stillExists = state.selectedDeviceId
      && state.devices.items.some(d => d.id === state.selectedDeviceId);
  if (!stillExists) {
    state.selectedDeviceId = state.devices.default_id;
  }
  renderDevicesList();
}
async function loadModels() {
  state.models = await getJSON('/api/models');
  state.selectedModelId = state.models.default_id;
  renderModelsList();
}

function populatePhase0() {
  // device dropdown — label each option with its transport (USB/BLE) so
  // the operator can see at a glance which row connects how.  Without
  // the explicit prefix it's easy to pick the wrong device and end up
  // with a BLE connect attempt when you wanted USB.
  const dSel = document.getElementById('fDevice');
  dSel.innerHTML = '';
  const TRANSPORT_LABEL = {
    'usb_auto':  'USB',
    'manual':    'USB',
    'bluetooth': 'BT (SPP)',
    'ble':       'BLE',
  };
  for (const d of state.devices.items) {
    const opt = document.createElement('option');
    opt.value = d.id;
    const transport = TRANSPORT_LABEL[d.connection_type] || d.connection_type || '?';
    opt.textContent = `[${transport}] ${d.name} · ${d.device_id}`
                    + (d.is_default ? '  (default)' : '');
    dSel.appendChild(opt);
  }
  if (state.selectedDeviceId) dSel.value = state.selectedDeviceId;
  dSel.onchange = () => { state.selectedDeviceId = dSel.value; updateDeviceHelp(); };
  updateDeviceHelp();

  // preset dropdown
  const pSel = document.getElementById('fPreset');
  pSel.innerHTML = '';
  for (const p of state.presets.items) {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = `${p.name}` + (p.is_default ? '  (default)' : '');
    pSel.appendChild(opt);
  }
  if (state.selectedPresetId) pSel.value = state.selectedPresetId;
  pSel.onchange = async () => {
    state.selectedPresetId = pSel.value;
    updatePresetHelp();
    // CRITICAL: also tell the server about the new preset.  /api/measure/start
    // does not accept a preset in its body — it reads _STATE.preset that was
    // set during /api/session/start.  Without this re-POST, switching the
    // dropdown updates the UI but the next measurement still runs with
    // the OLD preset (e.g. drop-detect after the user picked manual).
    if (state.connectionOpen) {
      try {
        await postJSON('/api/session/start', {
          operator:    val('fOperator') || state.operator || '',
          device_id:   state.selectedDeviceId,
          preset_id:   state.selectedPresetId,
        });
      } catch (e) {
        console.warn('preset refresh on server failed', e);
      }
    }
  };
  updatePresetHelp();
}

function updateDeviceHelp() {
  const d = state.devices.items.find(x => x.id === state.selectedDeviceId);
  setText('deviceHelp',
    d ? `${d.model || '—'} · port ${d.port_hint || 'auto-detect'}` : '—');
}

function updatePresetHelp() {
  const p = state.presets.items.find(x => x.id === state.selectedPresetId);
  if (!p) { setText('presetHelp', '—'); return; }
  const cv = p.cv || {};
  const trig = p.trigger_mode === 'manual' ? 'manual start' : 'hardware drop-detect';
  const timing = [];
  if (p.start_countdown_s > 0) timing.push(`${p.start_countdown_s}s start countdown`);
  if (p.post_drop_settle_s > 0) timing.push(`${p.post_drop_settle_s}s post-drop settle`);
  const timingStr = timing.length ? ' · ' + timing.join(' · ') : '';
  setText('presetHelp',
    `${cv.n_scans || 1} cycle(s) at ${cv.scan_rate} V/s · ${trig}${timingStr}`);
}

/* -------------------------------------------------------------------------
 * Manage modals
 * ----------------------------------------------------------------------- */

function openManageDevices() { renderDevicesList(); show('modalDevices'); }
function openManagePresets() {
  renderPresetsList();
  // Initialize the range picker if it hasn't been touched yet (e.g. on
  // first open with no preset loaded for editing).
  if (!state.presetRangePicker) {
    initRangePickerFrom({
      current_range: '100n', auto_range_low: '100n',
      auto_range_high: '1m', enable_autoranging: true,
    });
  } else {
    renderRangePicker();
  }
  show('modalPresets');
}
function openManageModels()  { renderModelsList();  show('modalModels'); }

function show(id) { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

function renderDevicesList() {
  const el = document.getElementById('devicesList');
  if (!el) return;
  el.innerHTML = '';
  for (const d of state.devices.items) {
    const conn = d.connection_type || 'usb_auto';
    const connLabel = {
      usb_auto:  'USB (auto)',
      ble:       'BLE',
      bluetooth: 'Bluetooth Classic',
      manual:    'Manual',
    }[conn] || conn;
    const portInfo = d.port_hint ? ` · ${escape(d.port_hint)}` : '';
    const isEditing = state.editingDeviceId === d.id;
    const row = document.createElement('div');
    row.className = 'manage-row clickable' + (d.is_default ? ' default' : '')
                    + (isEditing ? ' editing' : '');
    row.innerHTML = `
      <div onclick="loadDeviceIntoForm('${escape(d.id)}')" style="cursor:pointer;flex:1;">
        <div class="manage-row-name">
          ${escape(d.name)}
          ${d.is_default ? '<span class="badge">DEFAULT</span>' : ''}
          ${isEditing ? '<span class="badge" style="background:var(--amber-bg);color:var(--amber);">EDITING</span>' : ''}
        </div>
        <div class="manage-row-meta">
          <strong>Device:</strong> ${escape(d.device_id)} · ${escape(d.model || '')}
          &nbsp;·&nbsp;
          <strong>Connection:</strong> ${connLabel}${portInfo}
        </div>
      </div>
      <div class="manage-row-actions">
        ${d.is_default ? '' : `<button class="btn-link" onclick="setDefaultDevice('${d.id}')">Set default</button>`}
        <button class="btn-link danger" onclick="deleteDevice('${d.id}')">Delete</button>
      </div>`;
    el.appendChild(row);
  }
}

/* Pre-fill the device form with the values of an existing record so the
 * operator can edit + Update (or save as new). */
function loadDeviceIntoForm(id) {
  const d = state.devices.items.find(x => x.id === id);
  if (!d) return;
  state.editingDeviceId = id;
  setIfExists('newDeviceName', d.name || '');
  setIfExists('newDeviceId', d.device_id || '');
  setIfExists('newDeviceModel', d.model || 'EmStat4T');
  setIfExists('newDeviceConnType', d.connection_type || 'usb_auto');
  setIfExists('newDevicePort', d.port_hint || '');
  const dEl = document.getElementById('newDeviceDefault');
  if (dEl) dEl.checked = !!d.is_default;
  onDeviceConnTypeChange();
  const titleEl = document.getElementById('deviceFormTitle');
  if (titleEl) titleEl.textContent = `Editing: ${d.name}`;
  const btn = document.getElementById('btnAddDevice');
  if (btn) btn.textContent = 'Update device';
  renderDevicesList();
  toast('Editing device.', 'success');
}

function clearDeviceForm() {
  state.editingDeviceId = null;
  setIfExists('newDeviceName', '');
  setIfExists('newDeviceId', '');
  setIfExists('newDeviceModel', 'EmStat4T');
  setIfExists('newDeviceConnType', 'usb_auto');
  setIfExists('newDevicePort', '');
  const dEl = document.getElementById('newDeviceDefault');
  if (dEl) dEl.checked = false;
  onDeviceConnTypeChange();
  const titleEl = document.getElementById('deviceFormTitle');
  if (titleEl) titleEl.textContent = 'Add new device';
  const btn = document.getElementById('btnAddDevice');
  if (btn) btn.textContent = 'Add device';
  renderDevicesList();
}

function renderPresetsList() {
  const el = document.getElementById('presetsList');
  if (!el) return;
  el.innerHTML = '';
  for (const p of state.presets.items) {
    const cv = p.cv || {};
    const trig = p.trigger_mode === 'manual' ? 'manual' : 'drop-detect';
    const isEditing = state.editingPresetId === p.id;
    const row = document.createElement('div');
    row.className = 'manage-row clickable' + (p.is_default ? ' default' : '')
                    + (isEditing ? ' editing' : '');
    // Click anywhere on the row body (not the action buttons) to load
    // this preset's values into the form for editing.
    row.innerHTML = `
      <div onclick="loadPresetIntoForm('${escape(p.id)}')" style="cursor:pointer;flex:1;">
        <div class="manage-row-name">${escape(p.name)} ${p.is_default ? '<span class="badge">DEFAULT</span>' : ''}${isEditing ? '<span class="badge" style="background:var(--amber-bg);color:var(--amber);">EDITING</span>' : ''}</div>
        <div class="manage-row-meta">${cv.n_scans || 1} cycles · ${cv.scan_rate} V/s · trigger: ${trig}</div>
      </div>
      <div class="manage-row-actions">
        ${p.is_default ? '' : `<button class="btn-link" onclick="setDefaultPreset('${p.id}')">Set default</button>`}
        <button class="btn-link danger" onclick="deletePreset('${p.id}')">Delete</button>
      </div>`;
    el.appendChild(row);
  }
}

/* ============================================================
 * Current-range picker (PSTrace-style)
 * ============================================================
 * Each EmStat4T hardware range is a button.  Click rules:
 *   - unselected → select it; if no start range yet, become the start.
 *   - selected (not start) → become the start range (▼ moves here).
 *   - selected AND is start → unselect it; start moves to the
 *     adjacent selected range (or null if none left).
 *
 * On preset save, the picker state is translated to the existing
 * `current_range` / `auto_range_low` / `auto_range_high` /
 * `enable_autoranging` JSON fields.  Non-contiguous selections are
 * stored as their continuous bounding span (script builder doesn't
 * support skip-ranges yet) — visible in the UI but mathematically
 * the same as a contiguous selection.
 */
const ALL_RANGES = ['1n', '10n', '100n', '1u', '10u', '100u', '1m', '10m'];

function clickRangeBtn(range) {
  if (!state.presetRangePicker) {
    state.presetRangePicker = { selected: new Set(), start: null };
  }
  const p = state.presetRangePicker;
  if (!p.selected.has(range)) {
    p.selected.add(range);
    if (!p.start) p.start = range;
  } else if (p.start !== range) {
    p.start = range;
  } else {
    p.selected.delete(range);
    if (p.selected.size > 0) {
      const remaining = ALL_RANGES.filter(r => p.selected.has(r));
      p.start = remaining[0];
    } else {
      p.start = null;
    }
  }
  renderRangePicker();
}

function renderRangePicker() {
  const p = state.presetRangePicker || { selected: new Set(), start: null };
  document.querySelectorAll('#newPresetRangePicker .range-btn').forEach(btn => {
    const r = btn.dataset.range;
    btn.classList.toggle('selected', p.selected.has(r));
    btn.classList.toggle('is-start', p.start === r);
  });
  // Keep the hidden input synced for legacy callers.
  const hidden = document.getElementById('newPresetRange');
  if (hidden) hidden.value = p.start || '';
}

/* Initialize the picker from a preset's current_range + autorange bounds. */
function initRangePickerFrom(cv) {
  cv = cv || {};
  const start = cv.current_range || '100n';
  const lowIdx  = ALL_RANGES.indexOf(cv.auto_range_low  || start);
  const highIdx = ALL_RANGES.indexOf(cv.auto_range_high || start);
  const startIdx = ALL_RANGES.indexOf(start);
  const selected = new Set();
  if (cv.enable_autoranging !== false && lowIdx >= 0 && highIdx >= 0) {
    for (let i = Math.min(lowIdx, highIdx); i <= Math.max(lowIdx, highIdx); i++) {
      selected.add(ALL_RANGES[i]);
    }
  } else if (startIdx >= 0) {
    selected.add(start);
  }
  state.presetRangePicker = { selected, start: selected.has(start) ? start : null };
  renderRangePicker();
}

/* Translate picker state back into the four preset JSON fields. */
function rangePickerToCv() {
  const p = state.presetRangePicker || { selected: new Set(), start: null };
  if (p.selected.size === 0) {
    return {
      current_range: '100u',
      auto_range_low: '1n',
      auto_range_high: '100u',
      enable_autoranging: true,
    };
  }
  const sorted = ALL_RANGES.filter(r => p.selected.has(r));
  return {
    current_range: p.start || sorted[0],
    auto_range_low: sorted[0],
    auto_range_high: sorted[sorted.length - 1],
    enable_autoranging: sorted.length > 1,
  };
}

/* Pre-fill the "Add new preset" form with the values of an existing
 * preset so the operator can edit + update (or save as new). */
function loadPresetIntoForm(id) {
  const p = state.presets.items.find(x => x.id === id);
  if (!p) return;
  const cv = p.cv || {};
  state.editingPresetId = id;
  setIfExists('newPresetName', p.name || '');
  setIfExists('newPresetTrigger', p.trigger_mode || 'drop_detect');
  setIfExists('newPresetDropMethod', p.drop_detect_method || 'voltage');
  setIfExists('newPresetVoltageThreshold', p.voltage_threshold_mv ?? 30);
  setIfExists('newPresetN', cv.n_scans ?? 3);
  setIfExists('newPresetScanRate', cv.scan_rate ?? 0.1);
  setIfExists('newPresetEBegin', cv.e_begin ?? 0);
  setIfExists('newPresetEv1', cv.e_vtx1 ?? 0.5);
  setIfExists('newPresetEv2', cv.e_vtx2 ?? -0.5);
  setIfExists('newPresetStep', cv.e_step ?? 0.01);
  setIfExists('newPresetPretreatS', cv.pretreat_duration_s ?? 3);
  setIfExists('newPresetRange', cv.current_range || '100u');
  // Hydrate the PSTrace-style range picker from the preset's autorange bounds
  initRangePickerFrom(cv);
  setIfExists('newPresetStartCountdown', p.start_countdown_s ?? 0);
  setIfExists('newPresetPostDropSettle', p.post_drop_settle_s ?? 0);
  const dEl = document.getElementById('newPresetDefault');
  if (dEl) dEl.checked = !!p.is_default;
  onPresetTriggerChange();
  // Update form heading + button label
  const titleEl = document.getElementById('presetFormTitle');
  if (titleEl) titleEl.textContent = `Editing: ${p.name}`;
  const btn = document.getElementById('btnAddPreset');
  if (btn) btn.textContent = 'Update preset';
  renderPresetsList();
  toast('Loaded preset for editing.', 'success');
}

function renderModelsList() {
  const el = document.getElementById('modelsList');
  if (!el) return;
  el.innerHTML = '';
  for (const m of state.models.items) {
    const ions = (m.ions || []).map(i => i.symbol).join(', ');
    const status = m.kind === 'linear' ? 'built-in' :
                   (m.available ? 'file present' : 'FILE MISSING — using stub');
    const row = document.createElement('div');
    row.className = 'manage-row' + (m.is_default ? ' default' : '');
    row.innerHTML = `
      <div>
        <div class="manage-row-name">${escape(m.name)} ${m.is_default ? '<span class="badge">DEFAULT</span>' : ''}</div>
        <div class="manage-row-meta">${escape(m.kind)} · ions: ${escape(ions)} · ${status}</div>
        ${m.path ? `<div class="manage-row-meta" style="font-size:11px;">${escape(m.path)}</div>` : ''}
      </div>
      <div class="manage-row-actions">
        ${m.is_default ? '' : `<button class="btn-link" onclick="setDefaultModel('${m.id}')">Set default</button>`}
        ${m.kind === 'linear' ? '' : `<button class="btn-link danger" onclick="deleteModel('${m.id}')">Delete</button>`}
      </div>`;
    el.appendChild(row);
  }
}

async function setDefaultDevice(id) { await postJSON(`/api/devices/${id}/set_default`, {}); await loadDevices(); populatePhase0(); }
async function setDefaultPreset(id) { await postJSON(`/api/presets/${id}/set_default`, {}); await loadPresets(); populatePhase0(); }
async function setDefaultModel(id)  { await postJSON(`/api/models/${id}/set_default`, {});  await loadModels();  preparePhase4(); }
async function deleteDevice(id) {
  const dev = state.devices.items.find(x => x.id === id);
  let body;
  if (dev && dev.is_default) {
    const pw = prompt(
      'This is the default device.\n' +
      'Enter the admin password to delete it (see PASSWORDS.txt):'
    );
    if (pw === null) return;        // user cancelled
    body = { password: pw };
  } else {
    if (!confirm('Delete this device?')) return;
  }
  const r = await fetchDelete(`/api/devices/${id}`, body);
  if (r && r.ok === false) {
    toast(r.error || 'Delete failed.', 'error');
    return;
  }
  await loadDevices(); populatePhase0();
  toast('Device deleted.', 'success');
}
async function deletePreset(id) { await fetchDelete(`/api/presets/${id}`); await loadPresets(); populatePhase0(); }
async function deleteModel(id)  { await fetchDelete(`/api/models/${id}`);  await loadModels();  preparePhase4(); }

async function addDevice() {
  const name = val('newDeviceName'), did = val('newDeviceId');
  if (!name || !did) { toast('Name and ID required.', 'error'); return; }
  const conn = val('newDeviceConnType') || 'usb_auto';
  const portHint = val('newDevicePort') || '';
  if ((conn === 'ble' || conn === 'bluetooth') && !portHint) {
    toast('Device name or MAC required.', 'error'); return;
  }
  if (conn === 'manual' && !portHint) {
    toast('Port path required.', 'error'); return;
  }
  const body = {
    name, device_id: did,
    model: val('newDeviceModel') || 'EmStat4T',
    connection_type: conn,
    port_hint: portHint,
    set_default: chk('newDeviceDefault'),
  };

  if (state.editingDeviceId) {
    await fetch(`/api/devices/${state.editingDeviceId}`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    if (chk('newDeviceDefault')) {
      await postJSON(`/api/devices/${state.editingDeviceId}/set_default`, {});
    }
    toast('Device updated.', 'success');
  } else {
    await postJSON('/api/devices', body);
    toast('Device added.', 'success');
  }
  await loadDevices(); populatePhase0();
  clearDeviceForm();
}

/* Show / hide + relabel the port-hint field based on connection type. */
function onDeviceConnTypeChange() {
  const conn = val('newDeviceConnType');
  const wrap = document.getElementById('newDevicePortHintWrap');
  const lbl  = document.getElementById('newDevicePortHintLabel');
  const inp  = document.getElementById('newDevicePort');
  if (!wrap || !lbl || !inp) return;
  if (conn === 'usb_auto') {
    wrap.style.display = 'none';
    inp.value = '';
  } else if (conn === 'bluetooth' || conn === 'ble') {
    wrap.style.display = '';
    lbl.textContent = 'Device name or MAC';
    inp.placeholder = 'e.g., PS-539B';
  } else {   // manual
    wrap.style.display = '';
    lbl.textContent = 'Port path';
    inp.placeholder = '/dev/cu.usbmodem101 or COM6';
  }
}

async function addPreset() {
  const name = val('newPresetName');
  if (!name) { toast('Preset name required.', 'error'); return; }
  // Pull current_range / auto_range_low / auto_range_high / enable_autoranging
  // from the PSTrace-style picker rather than the (now-hidden) text input.
  const rangeCv = rangePickerToCv();
  const body = {
    name,
    technique: 'CV',
    trigger_mode: val('newPresetTrigger') || 'drop_detect',
    drop_detect_method: val('newPresetDropMethod') || 'voltage',
    voltage_threshold_mv: parseInt(val('newPresetVoltageThreshold'), 10) || 30,
    start_countdown_s: parseInt(val('newPresetStartCountdown'), 10) || 0,
    post_drop_settle_s: parseInt(val('newPresetPostDropSettle'), 10) || 0,
    cv: {
      e_begin: parseFloat(val('newPresetEBegin')) || 0,
      e_vtx1: parseFloat(val('newPresetEv1')) || 0.5,
      e_vtx2: parseFloat(val('newPresetEv2')) || -0.5,
      e_step: parseFloat(val('newPresetStep')) || 0.01,
      scan_rate: parseFloat(val('newPresetScanRate')) || 0.1,
      n_scans: parseInt(val('newPresetN'), 10) || 3,
      pretreat_duration_s: parseFloat(val('newPresetPretreatS')) || 0,
      pretreat_potential_v: 0,
      pretreat_interval_s: 0.1,
      pgstat_mode: 2,
      max_bandwidth_hz: 40,
      ...rangeCv,
    },
    set_default: chk('newPresetDefault'),
  };

  if (state.editingPresetId) {
    // Update existing preset in place
    await fetch(`/api/presets/${state.editingPresetId}`, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    if (chk('newPresetDefault')) {
      await postJSON(`/api/presets/${state.editingPresetId}/set_default`, {});
    }
    toast('Preset updated.', 'success');
  } else {
    await postJSON('/api/presets', body);
    toast('Preset added.', 'success');
  }
  await loadPresets(); populatePhase0(); renderPresetsList();
  clearPresetForm();
}

/* Visibility for the drop-detect-method/threshold fields based on the
 * current trigger mode + method selection in the preset modal. */
function onPresetTriggerChange() {
  const mode = val('newPresetTrigger');
  document.getElementById('presetDropMethodWrap').style.display =
    (mode === 'drop_detect') ? '' : 'none';
  onPresetDropMethodChange();
}
function onPresetDropMethodChange() {
  const mode = val('newPresetTrigger');
  const method = val('newPresetDropMethod');
  const showThr = (mode === 'drop_detect' && method === 'voltage');
  document.getElementById('presetVoltageThresholdWrap').style.display =
    showThr ? '' : 'none';
}

function clearPresetForm() {
  state.editingPresetId = null;
  ['newPresetName','newPresetVoltageThreshold','newPresetN','newPresetScanRate',
   'newPresetEBegin','newPresetEv1','newPresetEv2','newPresetStep',
   'newPresetPretreatS','newPresetRange','newPresetStartCountdown',
   'newPresetPostDropSettle']
    .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  // Restore sensible defaults
  setIfExists('newPresetVoltageThreshold', 30);
  setIfExists('newPresetN', 3);
  setIfExists('newPresetScanRate', 0.1);
  setIfExists('newPresetEBegin', 0);
  setIfExists('newPresetEv1', 0.5);
  setIfExists('newPresetEv2', -0.5);
  setIfExists('newPresetStep', 0.01);
  setIfExists('newPresetPretreatS', 3);
  setIfExists('newPresetRange', '100u');
  // Reset the PSTrace-style range picker to a sensible default span.
  initRangePickerFrom({
    current_range: '100n', auto_range_low: '100n',
    auto_range_high: '1m', enable_autoranging: true,
  });
  setIfExists('newPresetStartCountdown', 0);
  setIfExists('newPresetPostDropSettle', 0);
  const dEl = document.getElementById('newPresetDefault');
  if (dEl) dEl.checked = false;
  const tSel = document.getElementById('newPresetTrigger');
  if (tSel) tSel.value = 'drop_detect';
  const dmSel = document.getElementById('newPresetDropMethod');
  if (dmSel) dmSel.value = 'voltage';
  onPresetTriggerChange();
  // Update Add button label
  const btn = document.getElementById('btnAddPreset');
  if (btn) btn.textContent = 'Add preset';
  const titleEl = document.getElementById('presetFormTitle');
  if (titleEl) titleEl.textContent = 'Add new preset';
}

function setIfExists(id, v) {
  const el = document.getElementById(id);
  if (el) el.value = v;
}

async function addModel() {
  const name = val('newModelName'), path = val('newModelPath');
  if (!name || !path) { toast('Name and absolute file path required.', 'error'); return; }
  await postJSON('/api/models', {
    name, path,
    notes: val('newModelNotes') || '',
    set_default: chk('newModelDefault'),
  });
  await loadModels(); preparePhase4();
  ['newModelName','newModelPath','newModelNotes'].forEach(id => document.getElementById(id).value = '');
  toast('Model registered.', 'success');
}

/* -------------------------------------------------------------------------
 * Phase 0 — Begin session
 * ----------------------------------------------------------------------- */

/* Step 1 of 2: open the device connection and play the beep test.
 * The "Begin session" button stays disabled until this succeeds. */
async function connectDevice() {
  const op = val('fOperator');
  const errEl = document.getElementById('sessionError');
  errEl.classList.add('hidden');
  if (!op) {
    errEl.textContent = 'Operator name is required before connecting.';
    errEl.classList.remove('hidden'); return;
  }

  // Register the session first (so the backend knows which device record
  // / preset to use for the connection).  This is idempotent.
  try {
    const d = await postJSON('/api/session/start', {
      operator: op,
      device_id: state.selectedDeviceId,
      preset_id: state.selectedPresetId,
    });
    if (!d.ok) {
      errEl.textContent = d.error || 'Failed to register session.';
      errEl.classList.remove('hidden'); return;
    }
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden'); return;
  }

  const connBtn  = document.getElementById('btnConnectDevice');
  const startBtn = document.getElementById('btnStartSession');
  const orig = connBtn.textContent;
  connBtn.disabled = true;
  connBtn.textContent = 'Connecting…';

  // Transport-aware UX:
  //   USB: silent inline — just the button text changes.  No modal.
  //   BLE: modal with progress message because pairing can take up to a
  //        minute and the operator needs to watch for the macOS dialog.
  const selectedDev = (state.devices && state.devices.items || [])
      .find(d => d.id === state.selectedDeviceId);
  const isBle = !!selectedDev && selectedDev.connection_type === 'ble';
  if (isBle) {
    showConnectModal();
    setConnectModalText(
      'Connecting to device…',
      'First Bluetooth connection may take up to a minute — watch for ' +
      'the macOS pairing dialog.'
    );
  }

  // Hard timeout — even if the server hangs (e.g. firmware doesn't emit a
  // clean end-of-script after the beep, USB serial driver stuck), the UI
  // must always recover.  USB: 15 s.  BLE: 90 s (allows for first-pair).
  const TIMEOUT_MS = isBle ? 90000 : 15000;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);

  const restoreButton = () => {
    connBtn.textContent = orig;
    connBtn.disabled = false;
  };
  // Critical: a Connect FAILURE must invalidate any prior successful
  // connect — otherwise the operator can still hit Begin session with
  // a stale `state.connectionOpen = true` from an earlier session and
  // proceed against an unplugged / unreachable device.  This helper
  // flushes both the client flags AND any held connection on the
  // server (best-effort) so the next attempt starts from clean state.
  const invalidateConnection = async () => {
    state.connectionOpen = false;
    startBtn.disabled = true;
    // Optimistically refresh the sidebar pill to "Disconnected".
    const ps = document.getElementById('metaPalmsens');
    if (ps) {
      ps.textContent = '● Disconnected';
      ps.classList.remove('online');
      ps.classList.add('offline');
    }
    try { await postJSON('/api/connection/release', {}); } catch (e) { /* idempotent */ }
    refreshStatus();   // re-fetch authoritative server state
  };
  const showError = (msg) => {
    if (isBle) {
      setConnectModalText('Could not connect.', msg, 'error');
    } else {
      // No modal for USB — surface error inline + as a toast so operator
      // notices it without staring at a stuck dialog.
      const errEl = document.getElementById('sessionError');
      errEl.textContent = msg;
      errEl.classList.remove('hidden');
      toast(`Connect failed: ${msg}`, 'error');
    }
  };

  try {
    const resp = await fetch('/api/connection/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    const r = await resp.json();
    if (!r.ok) {
      await invalidateConnection();
      showError(r.error || 'Unknown error.');
      restoreButton();
      return;
    }
    const lines = r.info
      ? [`${r.info.device_type} · FW ${r.info.firmware_version}`,
         `S/N ${r.info.serial_number}`]
      : [];
    if (isBle) {
      setConnectModalText('Connected', lines.join('\n'), 'success');
      setTimeout(hideConnectModal, 1800);
    } else {
      // USB: silent success — the button + sidebar tell the story.
      toast('Connected.', 'success');
    }
    state.connectionOpen = true;
    connBtn.textContent = '✓ Connected';
    connBtn.disabled = true;
    startBtn.disabled = false;
    document.getElementById('signName').value = op;
    refreshStatus();
  } catch (e) {
    clearTimeout(timer);
    await invalidateConnection();
    const msg = (e.name === 'AbortError')
      ? `Connect timed out after ${TIMEOUT_MS / 1000}s.  ` +
        (isBle
          ? 'The BLE device may be paired with another host.'
          : 'Check the USB cable and that the EmStat4T is powered on.')
      : (e.message || 'Connect failed.');
    showError(msg);
    restoreButton();
  }
}

/* Step 2 of 2: navigate to Sample identification.  No more auto-connect. */
function startSession() {
  if (!state.connectionOpen) {
    const errEl = document.getElementById('sessionError');
    errEl.textContent = 'Click Connect first.';
    errEl.classList.remove('hidden');
    return;
  }
  goPhase(1);
}

/* Connect-progress modal */
function showConnectModal() {
  document.getElementById('modalConnect').classList.remove('hidden');
}
function hideConnectModal() {
  document.getElementById('modalConnect').classList.add('hidden');
  // reset for next time
  setConnectModalText('Connecting to device…', '', '');
}
function setConnectModalText(title, body, kind) {
  document.getElementById('connectModalTitle').textContent = title;
  document.getElementById('connectModalBody').textContent = body || '';
  const close = document.getElementById('connectModalClose');
  if (close) {
    if (kind === 'error') close.classList.remove('hidden');
    else close.classList.add('hidden');
  }
  const card = document.getElementById('connectModalCard');
  if (card) {
    card.classList.remove('error', 'success');
    if (kind) card.classList.add(kind);
  }
}

function resetSession() {
  document.getElementById('fOperator').value = '';
}

/* -------------------------------------------------------------------------
 * Phase 1 — QR scan
 * ----------------------------------------------------------------------- */

async function startQrScanner() {
  const video = document.getElementById('qrVideo');
  if (state.qrStream) return;
  try {
    state.qrStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment' }, audio: false,
    });
    video.srcObject = state.qrStream;
    state.qrCanvas = document.createElement('canvas');
    requestAnimationFrame(qrTick);
  } catch (e) {
    console.warn('Camera unavailable:', e);
  }
}

function stopQrScanner() {
  if (state.qrStream) {
    state.qrStream.getTracks().forEach(t => t.stop());
    state.qrStream = null;
  }
}

function qrTick() {
  if (!state.qrStream) return;
  const video = document.getElementById('qrVideo');
  if (video.readyState === video.HAVE_ENOUGH_DATA && typeof jsQR === 'function') {
    const c = state.qrCanvas;
    c.width = video.videoWidth; c.height = video.videoHeight;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(video, 0, 0, c.width, c.height);
    const img = ctx.getImageData(0, 0, c.width, c.height);
    const code = jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' });
    if (code && code.data) {
      // Cache a snapshot of the QR area for the confirm-card preview.
      // We crop a square around the detected QR location for a clean image.
      try {
        const loc = code.location;
        const xs = [loc.topLeftCorner.x, loc.topRightCorner.x,
                    loc.bottomLeftCorner.x, loc.bottomRightCorner.x];
        const ys = [loc.topLeftCorner.y, loc.topRightCorner.y,
                    loc.bottomLeftCorner.y, loc.bottomRightCorner.y];
        const minX = Math.max(0, Math.min(...xs) - 12);
        const minY = Math.max(0, Math.min(...ys) - 12);
        const maxX = Math.min(c.width,  Math.max(...xs) + 12);
        const maxY = Math.min(c.height, Math.max(...ys) + 12);
        const cropC = document.createElement('canvas');
        cropC.width = Math.max(1, maxX - minX);
        cropC.height = Math.max(1, maxY - minY);
        cropC.getContext('2d').drawImage(c, minX, minY, cropC.width, cropC.height,
                                         0, 0, cropC.width, cropC.height);
        state.scannedQrImage = cropC.toDataURL('image/png');
      } catch (e) {
        // Fall back to a snapshot of the whole frame
        state.scannedQrImage = c.toDataURL('image/png');
      }
      lookupSample(code.data.trim());
      return;
    }
  }
  requestAnimationFrame(qrTick);
}

function simulateScan() { lookupSample('DP-0001-V2'); }

/* Manual entry — opens the modal pre-filled with whatever we currently
 * know about the active sample (typically the QR scan result), so the
 * operator can edit / annotate without retyping everything. */
function openManualSampleEntry() {
  const meta = state.sample || {};
  document.getElementById('manSampleId').value  = meta.sample_id || '';
  document.getElementById('manDonor').value     = meta.donor_pouch || '';
  document.getElementById('manDate').value      = meta.collection_date || '';
  document.getElementById('manStorage').value   = meta.storage_location || '';
  document.getElementById('manAliquot').value   = meta.aliquot_purpose || '';
  document.getElementById('manHospital').value  = meta.hospital_site || '';
  document.getElementById('manNotes').value     = meta.notes || '';
  show('modalManualSample');
}

/* Discard the current sample and restart the QR scan. */
async function resetSampleScan() {
  await postJSON('/api/session/reset_sample', {});
  state.sample = null;
  document.getElementById('confirmCard').classList.add('hidden');
  document.getElementById('btnConfirmSample').disabled = true;
  // Re-show the mobile inline form so the operator can enter again.
  const inl = document.getElementById('inlineManualForm');
  if (inl) {
    inl.classList.remove('hidden');
    // Clear the previous values
    ['SampleId','Donor','Date','Storage','Aliquot','Hospital','Notes'].forEach(k => {
      const el = document.getElementById('inl_man' + k);
      if (el) el.value = '';
    });
  }
  setText('hdrSampleId', '—');
  startQrScanner();
  toast('Sample cleared.', 'success');
}

async function submitManualSample(prefix) {
  prefix = prefix || '';
  const v = (id) => val(prefix + id);
  const sid = v('manSampleId');
  if (!sid) { toast('Sample ID is required.', 'error'); return; }
  try {
    const d = await postJSON('/api/sample/manual', {
      sample_id: sid,
      donor_pouch: v('manDonor'),
      collection_date: v('manDate'),
      storage_location: v('manStorage'),
      aliquot_purpose: v('manAliquot'),
      hospital_site: v('manHospital'),
      notes: v('manNotes'),
    });
    if (!d.ok) { toast(d.error || 'Manual entry failed', 'error'); return; }
    state.sample = d.meta;
    setText('hdrSampleId', sid);
    renderConfirmCard(d.meta, 'MANUAL');
    document.getElementById('btnConfirmSample').disabled = false;
    closeModal('modalManualSample');
    // Mobile: hide the inline form once a sample is confirmed; the
    // confirmCard takes its place.
    const inl = document.getElementById('inlineManualForm');
    if (inl) inl.classList.add('hidden');
    stopQrScanner();
    toast('Sample registered manually.', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

/* Step 1: pop the non-blocking confirm modal.  We can't use the
 * built-in confirm() dialog because Chrome ignores it (or auto-dismisses
 * it) while a fetch SSE stream is still open — which is exactly when
 * the operator most needs to cancel. */
function cancelSession() {
  show('modalCancelConfirm');
}

/* Step 2: actually perform the cancel after the operator confirms. */
async function confirmCancelSession() {
  closeModal('modalCancelConfirm');
  // Closing animation: replay the splash video as the session winds down.
  showClosingSplash(2200);
  // Tear down any active SSE stream and standby timer first
  if (state.measureReader) {
    try { state.measureReader.cancel(); } catch (e) {}
    state.measureReader = null;
  }
  if (state._dropDetectStandbyTimer) {
    clearInterval(state._dropDetectStandbyTimer);
    state._dropDetectStandbyTimer = null;
  }
  // Backend: wipe everything
  await postJSON('/api/session/reset_all', {});
  // Clear local state
  state.measuredSamples = [];
  state.selectedAnalysisSampleId = null;
  state.inferenceSampleTicks = new Set();
  state.comparisonTicks = new Set();
  state.predictions = null;
  state.features = null;
  state.banner = 'ok';
  state.selectedIon = null;
  state.sample = null;
  state.scannedQrImage = null;
  state.maxPhaseReached = 0;
  state.connectionOpen = false;
  state.measureInProgress = false;
  state.measureFinished = false;
  // Re-enable Connect button, disable Begin session
  const cb = document.getElementById('btnConnectDevice');
  if (cb) { cb.disabled = false; cb.textContent = 'Connect'; }
  const sb = document.getElementById('btnStartSession');
  if (sb) sb.disabled = true;
  // Clear DOM artefacts that aren't tied to state
  const cc = document.getElementById('confirmCard');
  if (cc) cc.classList.add('hidden');
  const sc = document.getElementById('scannedQrCard');
  if (sc) sc.classList.add('hidden');
  const confirmBtn = document.getElementById('btnConfirmSample');
  if (confirmBtn) confirmBtn.disabled = true;
  setText('hdrSampleId', '—');
  goPhase(0);
  toast('Session cancelled.', 'success');
}

/* Phase 2 currently has no cancel-session button (it lives on Phase 1 now);
   this stub exists only because the old onclick may still be cached.   */
function cancelSessionPhase2() { goPhase(1); }

async function lookupSample(sampleId) {
  try {
    const d = await getJSON('/api/sample/lookup?sample_id=' + encodeURIComponent(sampleId));
    if (!d.ok) { toast(d.error || 'Lookup failed', 'error'); return; }
    state.sample = d.meta;
    setText('hdrSampleId', sampleId);
    renderConfirmCard(d.meta, d.from);
    document.getElementById('btnConfirmSample').disabled = false;
    stopQrScanner();
    toast('Sample identified.', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

function renderConfirmCard(meta, source) {
  const card = document.getElementById('confirmCard');
  const data = document.getElementById('confirmData');
  const badge = document.getElementById('confirmBadge');
  badge.textContent = source;
  badge.style.background = source === 'LIMS' ? 'var(--green-bg)' : 'var(--amber-bg)';
  badge.style.color      = source === 'LIMS' ? 'var(--green)'    : 'var(--amber)';

  // Freshness tag: clarifies whether this sample is staged for a new
  // acquisition, or whether it's actually one we've already measured
  // (in which case the measurement results are already in the batch).
  const freshTag = document.getElementById('confirmFreshTag');
  const sid = meta.sample_id;
  const alreadyMeasured =
    state.measuredSamples.some(s => s.sample_id === sid);
  if (alreadyMeasured) {
    freshTag.textContent = 'Already acquired in this session';
    freshTag.className = 'confirm-fresh-tag stale';
  } else {
    freshTag.textContent = 'Awaiting acquisition';
    freshTag.className = 'confirm-fresh-tag fresh';
  }

  data.innerHTML = '';
  for (const [label, key] of [
    ['Sample ID','sample_id'], ['Donor pouch','donor_pouch'],
    ['Collection date','collection_date'], ['Storage location','storage_location'],
    ['Aliquot purpose','aliquot_purpose'], ['Hospital site','hospital_site'],
  ]) {
    const row = document.createElement('div');
    row.className = 'confirm-row';
    row.innerHTML = `<span class="key">${label}</span><span class="val">${escape(meta[key] || '—')}</span>`;
    data.appendChild(row);
  }
  // Scanned QR image — show snapshot if we have one, otherwise fallback
  const img = document.getElementById('scannedQrImage');
  const fb  = document.getElementById('scannedQrFallback');
  if (state.scannedQrImage) {
    img.src = state.scannedQrImage;
    img.style.display = 'block';
    fb.style.display = 'none';
  } else {
    img.style.display = 'none';
    fb.style.display = 'block';
  }
  card.classList.remove('hidden');
}

/* -------------------------------------------------------------------------
 * Phase 2 — Sample loading (trigger mode driven by preset)
 * ----------------------------------------------------------------------- */

async function preparePhase2() {
  // Guard: refuse to enter Phase 2 if PalmSens isn't connected.
  if (!await ensurePalmsensConnected()) {
    goPhase(1);   // bounce back to Phase 1
    return;
  }
  const preset = state.presets.items.find(p => p.id === state.selectedPresetId)
              || state.presets.items.find(p => p.is_default);
  const trigger = (preset && preset.trigger_mode) || 'drop_detect';
  const settle = (preset && preset.post_drop_settle_s) || 0;
  const standby = (preset && preset.start_countdown_s) || 0;
  const phase3InProg = document.getElementById('phase3InProgress');
  const phase3Done   = document.getElementById('phase3Finished');
  if (phase3InProg) phase3InProg.classList.remove('hidden');
  if (phase3Done)   phase3Done.classList.add('hidden');

  const manualRow = document.getElementById('phase2Manual');
  const cancelRow = document.getElementById('phase2Cancel');
  if (cancelRow) cancelRow.classList.add('hidden');
  const illust = document.getElementById('loadIllustration');
  illust.classList.remove('ready', 'standby');

  if (trigger === 'manual') {
    // Manual: explicit Start button, no auto stream.
    manualRow.classList.remove('hidden');
    setText('loadHint',
      `Click Start measurement when ready.` +
      (standby > 0 ? ` A ${standby} s countdown runs first.` : ''));
    setText('loadHeadline', 'Manual');
    setText('loadSubline', 'Click Start measurement when ready.');
    setText('loadStatus', 'STANDBY');
    illust.classList.add('standby');
    return;
  }

  // Drop-detect: hide the manual button and run an automatic standby
  // countdown, then open the SSE stream which arms the chip's drop-detect
  // circuit.  When the chip reports "drop_detected", the SSE handler
  // navigates to Phase 3 and the in-script post-drop settle starts.
  manualRow.classList.add('hidden');
  setText('loadHint',
    `Place the sample once the indicator turns green.` +
    (settle > 0 ? ` Settle: ${settle} s.` : ''));
  setText('loadHeadline', 'Standby');
  setText('loadStatus', 'ARMING');
  illust.classList.add('standby');

  if (standby > 0) {
    let remaining = Math.max(1, parseInt(standby, 10));
    setText('loadSubline', `Arming — ${remaining} s`);
    state._dropDetectStandbyTimer && clearInterval(state._dropDetectStandbyTimer);
    state._dropDetectStandbyTimer = setInterval(() => {
      remaining -= 1;
      if (state.phase !== 2 || trigger !== 'drop_detect') {
        clearInterval(state._dropDetectStandbyTimer);
        return;
      }
      if (remaining <= 0) {
        clearInterval(state._dropDetectStandbyTimer);
        startMeasurement();   // SSE arms drop-detect → "drop_armed" → green
      } else {
        setText('loadSubline', `Arming — ${remaining} s`);
      }
    }, 1000);
  } else {
    setText('loadSubline', 'Arming…');
    setTimeout(() => {
      if (state.phase === 2) startMeasurement();
    }, 200);
  }
}

/* -------------------------------------------------------------------------
 * Phase 3 — Measurement (single SSE stream)
 * ----------------------------------------------------------------------- */

async function startMeasurement(opts) {
  opts = opts || {};
  // Guard: don't attempt to start without a PalmSens.
  if (!await ensurePalmsensConnected()) return;

  // Drop-detect mode keeps the operator on Phase 2 until the chip detects
  // liquid; manual mode jumps straight to Phase 3.  We figure that out
  // from the active preset (override allowed for the "Start now" escape
  // hatch — operator can force-skip drop-detect if it gets stuck).
  const preset = state.presets.items.find(p => p.id === state.selectedPresetId)
              || state.presets.items.find(p => p.is_default);
  const presetTrigger = (preset && preset.trigger_mode) || 'drop_detect';
  const trigger = opts.forceManual ? 'manual' : presetTrigger;
  if (trigger === 'manual') goPhase(3);
  // (drop-detect mode: stay on Phase 2 until SSE emits drop_detected,
  //  which then advances us to Phase 3 from handleSseEvent)

  state.measureInProgress = true;
  state.measureFinished = false;
  document.getElementById('phase3InProgress').classList.remove('hidden');
  document.getElementById('phase3Finished').classList.add('hidden');
  // Show the Phase 2 Cancel row whenever a measurement is in flight.
  const cancelRow = document.getElementById('phase2Cancel');
  if (cancelRow) cancelRow.classList.remove('hidden');
  // Hide the Phase 2 Manual button so the operator can't re-trigger
  document.getElementById('phase2Manual').classList.add('hidden');
  resetChart();

  fetch('/api/measure/start', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ force_manual: !!opts.forceManual }),
  }).then(resp => {
    if (!resp.ok) { toast('Measurement could not start.', 'error'); return; }
    state.measureReader = resp.body.getReader();
    consumeSSE(state.measureReader);
  }).catch(e => toast(e.message, 'error'));
}

/* (Previously had a "forceStartMeasurement" escape hatch that bypassed
 * drop-detect.  Removed at user request — drop-detect should just work,
 * not be worked around.  The server-side `force_manual` flag is kept
 * for now in case it's wanted later, but nothing in the UI calls it.) */

async function consumeSSE(reader) {
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLine = chunk.split('\n').find(l => l.startsWith('data:'));
      if (!dataLine) continue;
      let evt;
      try { evt = JSON.parse(dataLine.slice(5).trim()); }
      catch (e) { continue; }
      if (!evt.event) continue;
      handleSseEvent(evt);
      if (evt.event === 'finished' || evt.event === 'cancelled' || evt.event === 'error') return;
    }
  }
}

function handleSseEvent(evt) {
  switch (evt.event) {
    case 'countdown':
      // pre_script countdown for manual mode (Phase 3), post_drop settle
      // for both modes (Phase 3).
      if (evt.phase === 'post_drop') {
        // Switch to Phase 3 if we're still on the standby screen.
        if (state.phase === 2) goPhase(3);
        setText('progressStatus',
          `Sample settling — sweep begins in ${evt.remaining_s} s`);
        setMeasurementStatus('countdown',
          `Settling · ${evt.remaining_s} s`);
      } else {
        if (state.phase === 2) goPhase(3);
        setText('progressStatus',
          `Sweep begins in ${evt.remaining_s} s`);
        setMeasurementStatus('countdown',
          `Starting · ${evt.remaining_s} s`);
      }
      break;
    case 'drop_armed':
      if (evt.use_drop_detect) {
        // Stay on Phase 2; flip the indicator to green "ready".
        const illust = document.getElementById('loadIllustration');
        if (illust) {
          illust.classList.remove('standby');
          illust.classList.add('ready');
        }
        setText('loadHeadline', 'Awaiting sample');
        setText('loadSubline', 'Place the sample.');
        setText('loadStatus', 'READY');
      } else {
        // Manual mode: auto-advance to Phase 3 if not already there.
        if (state.phase !== 3) goPhase(3);
        setMeasurementStatus('armed', 'Starting');
        setText('progressStatus', 'starting measurement…');
      }
      break;
    case 'drop_detected':
      // Always advance from Phase 2 → Phase 3 on detection.
      if (state.phase !== 3) goPhase(3);
      setText('progressStatus', 'sample detected — measurement running');
      setMeasurementStatus('running', 'In progress');
      break;
    case 'sample':
      if (state.phase !== 3) goPhase(3);
      setMeasurementStatus('running', 'In progress');
      appendSample(evt);
      break;
    case 'scan_complete':
      // colour handled by per-cycle traces
      break;
    case 'finished':
      finishMeasurement(evt);
      break;
    case 'cancelled':
      cancelledMeasurement(evt);
      break;
    case 'error':
      toast(evt.message || 'Measurement error', 'error');
      setMeasurementStatus('error', 'Error');
      goPhase(2);
      break;
  }
}

/* Status pill on the Phase 3 chart card. */
function setMeasurementStatus(kind, label) {
  const el = document.getElementById('measureStatus');
  if (!el) return;
  el.className = 'status-pill ' + kind;
  el.textContent = label;
  // The card grows / shrinks when the status badge changes (e.g. on
  // "completed" the cancel button is replaced with a smaller status
  // pill).  Force Plotly to re-fit the chart so the curve doesn't
  // end up distorted.
  if (state.__chartResize) {
    setTimeout(state.__chartResize, 60);
  }
}

function cancelMeasurement() {
  // 1. Tell backend
  postJSON('/api/measure/cancel', {}).catch(() => {});
  // 2. Tear down reader so we stop listening
  if (state.measureReader) {
    try { state.measureReader.cancel(); } catch (e) {}
    state.measureReader = null;
  }
  // 3. Clear the standby timer if it's still running
  if (state._dropDetectStandbyTimer) {
    clearInterval(state._dropDetectStandbyTimer);
    state._dropDetectStandbyTimer = null;
  }
  state.measureInProgress = false;
  toast('Measurement cancelled.', 'success');
  goPhase(2);    // immediately back to sample loading
}

function cancelledMeasurement(evt) {
  state.measureInProgress = false;
  state.measureFinished = false;
  setMeasurementStatus('cancelled', 'Cancelled');
}

function finishMeasurement(evt) {
  state.measureInProgress = false;
  state.measureFinished = true;
  document.getElementById('progressFill').style.width = '100%';
  setText('progressStatus',
    `${evt.n_samples} samples acquired · saved to ${evt.csv_path}`);
  setMeasurementStatus('completed', 'Completed');
  document.getElementById('phase3InProgress').classList.add('hidden');
  document.getElementById('phase3Finished').classList.remove('hidden');
  // Record an auto-save event for the Phase 5 log.  We use the sample
  // currently identified at finish time (state.sample.sample_id) since
  // the just-acquired CSV is keyed on it.
  const sid = (state.sample && state.sample.sample_id) || (evt && evt.sample_id) || 'unknown';
  logSampleEvent('saved', sid, {
    n_samples: evt && evt.n_samples,
    csv_path: evt && evt.csv_path,
  });
  // Clear the "currently identified" sample so Phase 1 doesn't show stale
  // info if the operator goes back via the sidebar.  The acquired sample
  // is already in the batch list (Phase 4 / Phase 5).
  state.sample = null;
  state.scannedQrImage = null;
  toast('Measurement complete.', 'success');
}

/* -------------------------------------------------------------------------
 * Plotly live chart
 * ----------------------------------------------------------------------- */

/* matplotlib's "winter" colormap, ported to RGB:
 *   r = 0
 *   g = t
 *   b = 1 - 0.5 * t           (t in [0, 1] → blue → cyan-green)
 */
function winterColor(t) {
  const tt = Math.max(0, Math.min(1, t));
  const g = Math.round(tt * 255);
  const b = Math.round((1 - 0.5 * tt) * 255);
  return `rgb(0, ${g}, ${b})`;
}

function totalCyclesFromPreset() {
  const p = state.presets.items.find(x => x.id === state.selectedPresetId)
         || state.presets.items.find(x => x.is_default);
  const n = (p && p.cv && p.cv.n_scans) || 1;
  return Math.max(1, parseInt(n, 10));
}

function initLiveChart() {
  state.chartData = { x: [], y: [], scans: [] };
  state.chartScanIndex = new Map();
  state.chartTotalCycles = totalCyclesFromPreset();
  const layout = {
    margin: { t: 10, l: 70, r: 20, b: 50 },
    xaxis: { title: 'Potential (V)', zeroline: true, gridcolor: '#E8EBF2' },
    yaxis: { title: 'Current (A)', zeroline: true, gridcolor: '#E8EBF2',
             tickformat: '.2e' },
    showlegend: true,
    legend: { orientation: 'h', y: -0.18, x: 0,
              font: { size: 12 }, bgcolor: 'rgba(0,0,0,0)' },
    paper_bgcolor: '#FFFFFF',
    plot_bgcolor: '#FFFFFF',
    font: { family: '-apple-system, Inter, sans-serif', color: '#4A5275' },
  };
  // Pre-create one trace per expected cycle so the legend shows the full
  // set up front. New traces will be added on the fly if more arrive.
  const initial = [];
  for (let i = 0; i < state.chartTotalCycles; i++) {
    const c = winterColor(state.chartTotalCycles === 1
      ? 0 : i / (state.chartTotalCycles - 1));
    initial.push({
      x: [], y: [], mode: 'lines+markers', type: 'scatter',
      name: `Cycle ${i + 1}`,
      line: { color: c, width: 2 },
      marker: { color: c, size: 4 },
    });
    state.chartScanIndex.set(i, i);
  }
  Plotly.newPlot('liveChart', initial, layout,
                 { displayModeBar: false, responsive: true });
  state.chart = document.getElementById('liveChart');

  // Plotly's internal resize observer doesn't always re-fire when our
  // layout shifts (e.g. status badge appears, sidebar collapses on
  // mobile after the measurement completes).  Force a redraw on:
  //   • window resize / orientation change
  //   • after the measurement finishes (called from setMeasurementStatus)
  if (!state.__chartResizeHooked) {
    const redraw = () => {
      if (state.chart && document.body.contains(state.chart)) {
        try { Plotly.Plots.resize(state.chart); } catch (_) {}
      }
    };
    window.addEventListener('resize', redraw);
    window.addEventListener('orientationchange', redraw);
    state.__chartResize = redraw;
    state.__chartResizeHooked = true;
  }
}

function resetChart() {
  state.chartData = { x: [], y: [], scans: [] };
  state.chartScanIndex = new Map();
  document.getElementById('progressFill').style.width = '0%';
  setText('progressText', '0 samples');
  setText('progressStatus', '—');
  setMeasurementStatus('idle', 'Idle');
}

function _ensureTraceForScan(scan) {
  if (state.chartScanIndex.has(scan)) return state.chartScanIndex.get(scan);
  const total = Math.max(state.chartTotalCycles, scan + 1);
  state.chartTotalCycles = total;
  const t = total === 1 ? 0 : scan / (total - 1);
  const c = winterColor(t);
  Plotly.addTraces('liveChart', {
    x: [], y: [], mode: 'lines+markers', type: 'scatter',
    name: `Cycle ${scan + 1}`,
    line: { color: c, width: 2 },
    marker: { color: c, size: 4 },
  });
  // The new trace becomes the last one in the figure.
  const idx = (state.chart.data || []).length - 1;
  state.chartScanIndex.set(scan, idx);
  return idx;
}

function appendSample(evt) {
  state.chartData.x.push(evt.potential_v);
  state.chartData.y.push(evt.current_a);
  state.chartData.scans.push(evt.scan);
  if (state.chart) {
    const idx = _ensureTraceForScan(evt.scan || 0);
    Plotly.extendTraces('liveChart', {
      x: [[evt.potential_v]], y: [[evt.current_a]],
    }, [idx]);
  }
  setText('progressText', `${state.chartData.x.length} samples`);
  // Progress bar — based on expected sample count if we know it
  const target = expectedSampleCount();
  if (target > 0) {
    const pct = Math.min(100, Math.round(state.chartData.x.length / target * 100));
    document.getElementById('progressFill').style.width = pct + '%';
  }
  if (evt.status) {
    setText('progressStatus',
      `status: ${evt.status} · range: ${evt.range || '—'}`);
  }
}

function expectedSampleCount() {
  const p = state.presets.items.find(x => x.id === state.selectedPresetId)
         || state.presets.items.find(x => x.is_default);
  if (!p || !p.cv) return 0;
  const cv = p.cv;
  const path = Math.abs(cv.e_vtx1 - cv.e_begin)
             + Math.abs(cv.e_vtx2 - cv.e_vtx1)
             + Math.abs(cv.e_begin - cv.e_vtx2);
  const perCycle = Math.max(1, Math.round(path / Math.max(cv.e_step, 1e-9)));
  return perCycle * Math.max(1, cv.n_scans || 1);
}

/* -------------------------------------------------------------------------
 * Phase 4 — Inference
 * ----------------------------------------------------------------------- */

async function preparePhase4() {
  await loadModels();
  await loadMeasuredSamples();

  // Model picker
  const mSel = document.getElementById('fModel');
  if (mSel) {
    mSel.innerHTML = '';
    for (const m of state.models.items) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = `${m.name}` + (m.is_default ? '  (default)' : '');
      mSel.appendChild(opt);
    }
    if (state.selectedModelId) mSel.value = state.selectedModelId;
    mSel.onchange = () => { state.selectedModelId = mSel.value; updateModelHelp(); };
    updateModelHelp();
  }

  // Default: select the most-recent sample for the detail panel and tick
  // every sample for batch analysis.
  if (!state.selectedAnalysisSampleId && state.measuredSamples.length) {
    state.selectedAnalysisSampleId =
      state.measuredSamples[state.measuredSamples.length - 1].sample_id;
  }
  if (!state.inferenceSampleTicks) state.inferenceSampleTicks = new Set();
  for (const s of state.measuredSamples) state.inferenceSampleTicks.add(s.sample_id);

  populateCycleCheckboxes();
  renderInferenceSampleTable();
  renderIndividualList();
  renderComparison();
  renderIndividualDetail();
}

/* ---------- Inference model: sample tick table -------------------------- */

function renderInferenceSampleTable() {
  const tbody = document.getElementById('infSampleBody');
  const all = document.getElementById('infSampleAll');
  if (!tbody || !all) return;
  tbody.innerHTML = '';
  if (!state.measuredSamples.length) {
    tbody.innerHTML =
      '<tr><td colspan="5" style="padding:16px;color:var(--text-muted);">No acquisitions yet.</td></tr>';
    all.checked = false;
    return;
  }
  for (const s of state.measuredSamples) {
    const ticked = state.inferenceSampleTicks.has(s.sample_id);
    const status = s.analyzed
      ? `<span class="banner-tag ${s.banner}">${s.banner.toUpperCase()}</span>`
      : `<span class="banner-tag pending">PENDING</span>`;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="cb-cell">
        <input type="checkbox" data-inf-sample="${escape(s.sample_id)}"
               ${ticked ? 'checked' : ''}
               onchange="onInferenceSampleTick('${escape(s.sample_id)}', this.checked)" />
      </td>
      <td><strong>${escape(s.sample_id)}</strong></td>
      <td>${status}</td>
      <td class="muted">${escape(s.measured_at)}</td>
      <td>
        <button class="btn-link danger" onclick="deleteSampleInline('${escape(s.sample_id)}')">Delete</button>
      </td>`;
    tbody.appendChild(tr);
  }
  // "Tick all" reflects whether every row is ticked
  all.checked = state.measuredSamples.every(
    s => state.inferenceSampleTicks.has(s.sample_id));
}

function onInferenceSampleTick(sid, checked) {
  if (checked) state.inferenceSampleTicks.add(sid);
  else state.inferenceSampleTicks.delete(sid);
  // Update "all" state
  const all = document.getElementById('infSampleAll');
  if (all) all.checked = state.measuredSamples.every(
    s => state.inferenceSampleTicks.has(s.sample_id));
}

function toggleAllInferenceSamples() {
  const all = document.getElementById('infSampleAll');
  if (!all) return;
  if (all.checked) {
    for (const s of state.measuredSamples)
      state.inferenceSampleTicks.add(s.sample_id);
  } else {
    state.inferenceSampleTicks.clear();
  }
  renderInferenceSampleTable();
}

async function deleteSampleInline(sid) {
  if (!confirm(`Delete ${sid}? This will also remove the saved CSV on disk and any analysis result for it.`)) return;
  await fetchDelete(`/api/session/samples/${encodeURIComponent(sid)}`);
  state.inferenceSampleTicks.delete(sid);
  state.comparisonTicks?.delete(sid);
  state.comparisonSeen?.delete(sid);
  if (state.selectedAnalysisSampleId === sid) state.selectedAnalysisSampleId = null;
  await loadMeasuredSamples();
  renderInferenceSampleTable();
  renderIndividualList();
  renderComparison();
  renderIndividualDetail();
  logSampleEvent('deleted', sid);
  toast(`Sample ${sid} removed.`, 'success');
}

/* Single Run analysis — runs the active model on every ticked sample
 * with the current cycle selection. */
async function runAnalysis() {
  const ticked = state.measuredSamples.filter(
    s => state.inferenceSampleTicks.has(s.sample_id));
  if (!ticked.length) { toast('Tick at least one sample.', 'error'); return; }
  const cycles = selectedCycles();
  let n = 0;
  for (const s of ticked) {
    try {
      await postJSON('/api/predict', {
        model_id: state.selectedModelId,
        sample_id: s.sample_id,
        cycles: cycles,
      });
      n += 1;
    } catch (e) { console.warn('predict failed for', s.sample_id, e); }
  }
  await loadMeasuredSamples();
  // Re-running analysis with a subset of samples must visually narrow the
  // comparison view to that subset.  Replace (not union) the comparison
  // tick set so only just-analysed samples appear in the chart; reset
  // the "seen" guard so they get auto-ticked on next render.
  const justRunIds = new Set(ticked.map(s => s.sample_id));
  state.comparisonTicks = new Set(justRunIds);
  state.comparisonSeen  = new Set(justRunIds);
  renderInferenceSampleTable();
  renderIndividualList();
  renderComparison();
  renderIndividualDetail();
  toast(`Analysed ${n} sample(s).`, 'success');
}

async function loadMeasuredSamples() {
  const r = await getJSON('/api/session/samples');
  state.measuredSamples = r.items || [];
}

/* ---------- Individual list (one row per acquired sample) -------------- */

function renderIndividualList() {
  const el = document.getElementById('individualList');
  if (!el) return;
  if (!state.measuredSamples.length) {
    el.innerHTML =
      '<div class="batch-empty">No acquisitions yet.</div>';
    return;
  }
  // Apply text search
  const q = (document.getElementById('indivSearch')?.value || '').trim().toLowerCase();
  const filtered = q
    ? state.measuredSamples.filter(s => s.sample_id.toLowerCase().includes(q))
    : state.measuredSamples;
  if (!filtered.length) {
    el.innerHTML = '<div class="batch-empty">No samples match the search.</div>';
    return;
  }
  // Compact one-line rows; click to select.
  const rows = [];
  for (const s of filtered) {
    const isActive = s.sample_id === state.selectedAnalysisSampleId;
    const status = s.analyzed
      ? `<span class="banner-tag ${s.banner}">${s.banner.toUpperCase()}</span>`
      : `<span class="banner-tag pending">PENDING</span>`;
    rows.push(`
      <button class="indiv-pill ${isActive ? 'active' : ''}"
              onclick="selectIndividual('${escape(s.sample_id)}')">
        <span class="indiv-pill-id">${escape(s.sample_id)}</span>
        ${status}
        <span class="indiv-pill-meta">${s.n_samples} pts</span>
      </button>`);
  }
  el.innerHTML = rows.join('');
}

function selectIndividual(sid) {
  state.selectedAnalysisSampleId = sid;
  renderIndividualList();
  renderIndividualDetail();
}

function renderIndividualDetail() {
  const sid = state.selectedAnalysisSampleId;
  const s = state.measuredSamples.find(x => x.sample_id === sid);
  // Cards: hide everything until we have a sample (or its analysis result).
  const predEl = document.getElementById('predictionsCard');
  const expEl  = document.getElementById('experimentInfoCard');
  const featEl = document.getElementById('featuresCard');
  predEl.classList.add('hidden');
  expEl.classList.add('hidden');
  featEl.classList.add('hidden');
  document.getElementById('banner').classList.add('hidden');

  if (!s) return;
  if (s.analyzed && s.predictions) {
    renderPredictions({
      sample_id: s.sample_id,
      applied_cycles: [],
      primary: s.predictions,
      features: s.features || {},
      review_flags: s.review_flags || [],
      banner: s.banner || 'ok',
    });
  }
}

function populateCycleCheckboxes() {
  const el = document.getElementById('cycleCheckboxes');
  if (!el) return;
  const total = totalCyclesFromPreset();
  const html = [];
  // "Average all cycles" is the default; ticking a specific cycle disables avg
  html.push(`
    <label class="cb-item">
      <input type="checkbox" id="cyAll" checked
             onchange="onCycleCheckboxChange('all')" />
      <span>Average all (${total} cycles)</span>
    </label>`);
  for (let i = 0; i < total; i++) {
    html.push(`
      <label class="cb-item">
        <input type="checkbox" id="cy${i}" data-cycle="${i}"
               onchange="onCycleCheckboxChange(${i})" />
        <span>Cycle ${i + 1}</span>
      </label>`);
  }
  el.innerHTML = html.join('');
}

function onCycleCheckboxChange(which) {
  const all = document.getElementById('cyAll');
  if (which === 'all') {
    if (all.checked) {
      // Selecting "Average all" deselects individual ticks
      document.querySelectorAll('[data-cycle]').forEach(cb => cb.checked = false);
    }
  } else {
    // Selecting an individual cycle deselects "Average all"
    if (document.querySelector('[data-cycle]:checked')) all.checked = false;
    else all.checked = true;   // nothing selected → fall back to avg
  }
  // Re-render the chart immediately so Box/Swarm reflect the new cycle
  // selection (the table will catch up after Run analysis).
  if (state.selectedIon) plotIonAcrossSamples();
}

function selectedCycles() {
  const ticks = document.querySelectorAll('[data-cycle]:checked');
  if (!ticks.length) return null;       // null = average over all
  return Array.from(ticks).map(cb => parseInt(cb.dataset.cycle, 10));
}

async function runPredictionForAll() {
  // Run analysis once per acquired sample, with default (avg-of-all) cycles.
  if (!state.measuredSamples.length) {
    toast('No samples to analyse yet.', 'error'); return;
  }
  let n = 0;
  for (const s of state.measuredSamples) {
    try {
      await postJSON('/api/predict', {
        model_id: state.selectedModelId,
        sample_id: s.sample_id,
        cycles: null,
      });
      n += 1;
    } catch (e) { console.warn('predict failed for', s.sample_id, e); }
  }
  await loadMeasuredSamples();
  renderIndividualList();
  renderComparison();
  renderIndividualDetail();
  toast(`Analysed ${n} sample(s).`, 'success');
}

async function deleteSelectedSample() {
  const sid = state.selectedAnalysisSampleId;
  if (!sid) return;
  if (!confirm(`Delete the measurement for ${sid}? The saved CSV on disk will also be removed and you'll need to re-scan and re-acquire it.`)) return;
  await fetchDelete(`/api/session/samples/${encodeURIComponent(sid)}`);
  state.selectedAnalysisSampleId = null;
  state.inferenceSampleTicks?.delete(sid);
  state.comparisonTicks?.delete(sid);
  state.comparisonSeen?.delete(sid);
  await loadMeasuredSamples();
  renderIndividualList();
  renderComparison();
  renderIndividualDetail();
  logSampleEvent('deleted', sid);
  toast(`Sample ${sid} removed.`, 'success');
}

function updateModelHelp() {
  const m = state.models.items.find(x => x.id === state.selectedModelId);
  if (!m) { setText('modelHelp', '—'); return; }
  const ions = (m.ions || []).map(i => i.symbol).join(', ');
  const status = m.kind === 'linear' ? 'built-in' :
                 (m.available ? 'file present' : 'FILE MISSING — falling back to stub');
  setText('modelHelp',
    `${m.kind} · ions: ${ions} · ${status}`);
}

async function runPrediction() {
  const mSel = document.getElementById('fModel');
  if (mSel) state.selectedModelId = mSel.value || state.selectedModelId;
  const cycles = selectedCycles();
  try {
    const d = await postJSON('/api/predict', {
      model_id: state.selectedModelId,
      sample_id: state.selectedAnalysisSampleId,
      cycles: cycles,
    });
    if (!d.ok) { toast(d.error || 'Analysis failed', 'error'); return; }
    state.predictions = d;
    state.features = d.features;
    state.banner = d.banner;
    renderPredictions(d);
    await loadMeasuredSamples();
    renderIndividualList();
    renderComparison();
  } catch (e) { toast(e.message, 'error'); }
}

function renderPhase4(d) {
  // After running prediction in the new flow, also refresh the list +
  // overall comparison.
  renderPredictions(d);
  loadMeasuredSamples().then(() => {
    renderIndividualList();
    renderComparison();
  });
}

/* Experiment-info keys are surface-level facts about the run; they're
 * separated from the actual features the model consumes. */
const EXPERIMENT_INFO_KEYS = new Set([
  'n_samples', 'n_scans',
  'potential_min_v', 'potential_max_v',
  'current_mean_a', 'current_std_a',
]);

function renderPredictions(d) {
  const banner = document.getElementById('banner');
  banner.classList.remove('hidden', 'ok', 'review', 'anomaly');
  if (d.banner === 'review') {
    banner.classList.add('review');
    banner.innerHTML =
      '<strong>REVIEW</strong> — model & baseline disagree on: ' +
      escape(d.review_flags.join(', '));
    document.getElementById('reasonReq')?.classList.remove('hidden');
  } else if (d.banner === 'anomaly') {
    banner.classList.add('anomaly');
    banner.innerHTML =
      '<strong>OVERLOAD</strong> — current saturated. Data unreliable; ' +
      're-acquire with a wider range or autoranging.';
    document.getElementById('reasonReq')?.classList.remove('hidden');
  } else {
    banner.classList.add('ok');
    banner.textContent = 'All checks passed — primary and linear baseline within 30%.';
    document.getElementById('reasonReq')?.classList.add('hidden');
  }

  // Predictions grid
  const grid = document.getElementById('ionGrid');
  grid.innerHTML = '';
  for (const p of (d.primary.predictions || [])) {
    const card = document.createElement('div');
    card.className = 'ion-card' + (p.below_lod ? ' below-lod' : '');
    const value = p.below_lod
      ? `< ${p.lod_mg_l.toExponential(2)}`
      : `${p.value_mg_l.toFixed(3)} mg/L`;
    card.innerHTML =
      `<div class="symbol">${escape(p.ion)}</div>` +
      `<div class="name">${escape(p.name || 'ion')}</div>` +
      `<div class="value">${value}</div>` +
      `<div class="ci">${p.below_lod ? 'below LOD' :
         '± ' + (p.ci_high_mg_l - p.value_mg_l).toFixed(3) + ' mg/L'}</div>` +
      ((d.review_flags || []).includes(p.ion)
        ? `<div class="lod-flag" style="background: var(--amber-bg); color: var(--amber);">REVIEW</div>` : '');
    grid.appendChild(card);
  }
  document.getElementById('predictionsCard').classList.remove('hidden');
  setText('predForSample',
    d.applied_cycles && d.applied_cycles.length
      ? `${d.sample_id} · cycles ${d.applied_cycles.map(x => x + 1).join(', ')}`
      : `${d.sample_id}`);

  // Categorise features → Experiment info vs. Extracted features
  const features = d.features || {};
  const expInfo = {};
  const extracted = {};
  for (const [k, v] of Object.entries(features)) {
    if (EXPERIMENT_INFO_KEYS.has(k)) expInfo[k] = v;
    else extracted[k] = v;
  }
  const expGrid = document.getElementById('experimentInfo');
  expGrid.innerHTML = '';
  const PRETTY = {
    n_samples: 'Number of data points',
    n_scans: 'Number of cycles acquired',
    potential_min_v: 'Minimum potential (V)',
    potential_max_v: 'Maximum potential (V)',
    current_mean_a: 'Mean current (A)',
    current_std_a: 'Current std. dev. (A)',
  };
  for (const [k, v] of Object.entries(expInfo)) {
    const row = document.createElement('div');
    row.className = 'exp-info-row';
    let display = v;
    if (typeof v === 'number') {
      if (Math.abs(v) < 1e-3 && v !== 0) display = v.toExponential(3);
      else display = (Math.abs(v) > 1e6) ? v.toExponential(3) : v.toFixed(4);
    }
    row.innerHTML = `<span class="key">${escape(PRETTY[k] || k)}</span><span class="val">${escape(display)}</span>`;
    expGrid.appendChild(row);
  }
  document.getElementById('experimentInfoCard').classList.remove('hidden');

  document.getElementById('featuresCard').classList.remove('hidden');
  renderFeaturesTable(extracted);
  document.getElementById('featuresTable').classList.add('hidden');
  document.getElementById('featuresToggleBtn').textContent = 'Show features';
}

/* Render the extracted-features dict as a tabular two-column list (key,
 * value) instead of a raw JSON dump.  Numbers are formatted with sensible
 * precision; long ion-prefixed keys get human-readable labels. */
function renderFeaturesTable(features) {
  const tbl = document.getElementById('featuresTable');
  if (!tbl) return;
  const entries = Object.entries(features || {});
  if (!entries.length) {
    tbl.innerHTML = '<div class="features-empty">No features available.</div>';
    return;
  }
  // Pretty label: "cu_peak_height_a" → "Cu · Peak height (A)"
  const ION_PREFIXES = ['cu', 'pb', 'cd', 'zn', 'fe', 'hg'];
  const UNIT_HINTS = {
    a: '(A)', v: '(V)', s: '(s)',
    mg_l: '(mg/L)', mg: '(mg)', l: '(L)',
  };
  function pretty(key) {
    let prefix = '';
    let rest = key;
    const m = key.toLowerCase().match(/^([a-z]{1,2})_(.*)$/);
    if (m && ION_PREFIXES.includes(m[1])) {
      prefix = m[1].charAt(0).toUpperCase() + m[1].slice(1) + ' · ';
      rest = m[2];
    }
    // detect trailing unit hint (last token like _a, _v, _mg_l)
    const parts = rest.split('_');
    let unit = '';
    for (const len of [2, 1]) {
      if (parts.length > len) {
        const cand = parts.slice(-len).join('_');
        if (UNIT_HINTS[cand]) { unit = ' ' + UNIT_HINTS[cand]; parts.splice(-len); break; }
      }
    }
    const label = parts.join(' ').replace(/\b\w/g, c => c.toUpperCase());
    return prefix + label + unit;
  }
  function fmt(v) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'number') {
      if (!isFinite(v)) return String(v);
      if (v === 0) return '0';
      const abs = Math.abs(v);
      if (abs < 1e-3 || abs >= 1e6) return v.toExponential(3);
      if (abs < 1)   return v.toFixed(5);
      if (abs < 100) return v.toFixed(4);
      return v.toFixed(2);
    }
    if (Array.isArray(v)) return v.map(fmt).join(', ');
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }
  // Sort: ion-prefixed grouped by ion in canonical order, then alphabetical.
  entries.sort(([a], [b]) => {
    const ai = ION_PREFIXES.indexOf((a.match(/^([a-z]{1,2})_/i) || [,''])[1].toLowerCase());
    const bi = ION_PREFIXES.indexOf((b.match(/^([a-z]{1,2})_/i) || [,''])[1].toLowerCase());
    if (ai !== bi) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    return a.localeCompare(b);
  });
  let html = '<table class="features-tbl"><tbody>';
  for (const [k, v] of entries) {
    html += `<tr><th>${escape(pretty(k))}</th><td>${escape(fmt(v))}</td></tr>`;
  }
  html += '</tbody></table>';
  tbl.innerHTML = html;
}

/* Cross-sample comparison: ticked table + per-ion chart with kind switcher.
 * `state.comparisonTicks` records which sample IDs are visualised. */
function renderComparison() {
  const card = document.getElementById('comparisonCard');
  const empty = document.getElementById('overallEmptyCard');
  const tbody = document.getElementById('comparisonTable');
  const ionRow = document.getElementById('ionPickerRow');
  if (!card || !tbody || !ionRow) return;

  const analysed = state.measuredSamples.filter(s => s.analyzed && s.predictions);
  if (analysed.length === 0) {
    card.classList.add('hidden');
    if (empty) empty.classList.remove('hidden');
    return;
  }
  if (empty) empty.classList.add('hidden');
  card.classList.remove('hidden');

  // Default-tick every NEWLY analysed sample on first sighting.  Once a
  // sample has been seen by the comparison view we never re-tick it
  // automatically — otherwise un-ticking is undone on every re-render
  // (renderComparison is called after every toggle).
  if (!state.comparisonSeen) state.comparisonSeen = new Set();
  for (const s of analysed) {
    if (!state.comparisonSeen.has(s.sample_id)) {
      state.comparisonSeen.add(s.sample_id);
      state.comparisonTicks.add(s.sample_id);
    }
  }

  // Union of ions
  const ionSet = new Set();
  for (const s of analysed)
    for (const p of (s.predictions.predictions || [])) ionSet.add(p.ion);
  const ions = Array.from(ionSet);

  // ----- Table -----
  const allTicked = analysed.every(s => state.comparisonTicks.has(s.sample_id));
  const rows = [];
  rows.push('<table class="cmp-table"><thead><tr>' +
            `<th class="cb-cell"><input type="checkbox" ${allTicked ? 'checked' : ''} onchange="toggleAllComparison()" /></th>` +
            '<th>Sample</th>' +
            ions.map(i => `<th>${escape(i)} (mg/L)</th>`).join('') +
            '<th>Status</th></tr></thead><tbody>');
  for (const s of analysed) {
    const byIon = new Map((s.predictions.predictions || []).map(p => [p.ion, p]));
    const ticked = state.comparisonTicks.has(s.sample_id);
    rows.push(`<tr>
      <td class="cb-cell"><input type="checkbox" ${ticked ? 'checked' : ''}
           onchange="toggleComparison('${escape(s.sample_id)}', this.checked)" /></td>
      <td><strong>${escape(s.sample_id)}</strong></td>`);
    for (const ion of ions) {
      const p = byIon.get(ion);
      if (!p) { rows.push('<td>—</td>'); continue; }
      if (p.below_lod) rows.push('<td class="below-lod">&lt; LOD</td>');
      else rows.push(`<td>${p.value_mg_l.toFixed(3)}</td>`);
    }
    rows.push(`<td><span class="banner-tag ${s.banner}">${s.banner}</span></td></tr>`);
  }
  rows.push('</tbody></table>');
  tbody.innerHTML = rows.join('');

  // ----- Ion picker -----
  ionRow.innerHTML = '';
  for (const ion of ions) {
    const chip = document.createElement('button');
    chip.className = 'ion-chip';
    chip.textContent = ion;
    if (ion === state.selectedIon) chip.classList.add('active');
    chip.onclick = () => { state.selectedIon = ion; plotIonAcrossSamples(); };
    ionRow.appendChild(chip);
  }

  // Auto-pick the first ion on initial render so the chart isn't
  // mysteriously empty.  After analyses finish (e.g. cycles changed),
  // ALWAYS redraw the chart so the bar/box/swarm reflects the new
  // predictions — without this the table updates but the chart appears
  // stale.
  if (!state.selectedIon || !ions.includes(state.selectedIon)) {
    state.selectedIon = ions[0] || null;
  }
  if (state.selectedIon) plotIonAcrossSamples();
}

function toggleComparison(sid, checked) {
  if (checked) state.comparisonTicks.add(sid);
  else state.comparisonTicks.delete(sid);
  renderComparison();
}
function toggleAllComparison() {
  const analysed = state.measuredSamples.filter(s => s.analyzed && s.predictions);
  const allTicked = analysed.every(s => state.comparisonTicks.has(s.sample_id));
  if (allTicked) state.comparisonTicks.clear();
  else for (const s of analysed) state.comparisonTicks.add(s.sample_id);
  renderComparison();
}

function setChartKind(kind) {
  state.chartKind = kind;
  document.querySelectorAll('.seg-btn[data-chart]').forEach(b => {
    b.classList.toggle('active', b.dataset.chart === kind);
  });
  if (state.selectedIon) plotIonAcrossSamples();
}

/* Plot one ion across the ticked samples in the current chart kind. */
function plotIonAcrossSamples() {
  const wrap = document.getElementById('ionChart');
  if (!wrap) return;
  const ion = state.selectedIon;
  if (!ion) return;
  const analysed = state.measuredSamples
    .filter(s => s.analyzed && s.predictions
                  && state.comparisonTicks.has(s.sample_id));
  if (analysed.length === 0) { wrap.classList.add('hidden'); return; }
  wrap.classList.remove('hidden');

  // Aggregate value (avg-of-cycles) per sample — used by Bar.
  const aggValues = analysed.map(s =>
    (s.predictions.predictions || []).find(p => p.ion === ion));
  const aggXs = analysed.map(s => s.sample_id);

  // Per-cycle distribution per sample — used by Box and Swarm.
  // perCycle[i] = array of concentration values across cycles for sample i.
  // Filter the per-cycle predictions by the same cycles the operator
  // selected in the Active model section, so the chart and the table
  // are showing the same data.
  const cycleFilter = selectedCycles();   // null = all cycles
  const cyclesAllowed = cycleFilter ? new Set(cycleFilter) : null;
  const perCycle = analysed.map(s => {
    const arr = (s.per_cycle || [])
      .filter(pc => cyclesAllowed === null || cyclesAllowed.has(pc.cycle))
      .map(pc => {
        const p = (pc.predictions || []).find(p => p.ion === ion);
        return p ? p.value_mg_l : null;
      })
      .filter(v => v !== null);
    if (arr.length === 0) {
      const p = aggValues[analysed.indexOf(s)];
      return p ? [p.value_mg_l] : [];
    }
    return arr;
  });

  let traces = [];
  let layout = {
    title: { text: `${ion} concentration · ${analysed.length} sample(s)`,
             font: { size: 14, color: '#1A1F36' } },
    margin: { t: 50, l: 70, r: 20, b: 80 },
    yaxis: { title: 'Concentration (mg/L)',
             gridcolor: '#E8EBF2', tickformat: '.3f' },
    showlegend: false,
    paper_bgcolor: '#FFFFFF', plot_bgcolor: '#FFFFFF',
    font: { family: '-apple-system, Inter, sans-serif', color: '#4A5275' },
  };

  if (state.chartKind === 'bar') {
    // Per-sample bar (avg-of-cycles), with CI error bars.
    const ys = aggValues.map(p => p ? p.value_mg_l : 0);
    const errs = aggValues.map(p => p ? p.ci_high_mg_l - p.value_mg_l : 0);
    const colors = aggValues.map(p => p && p.below_lod ? '#B4BED4' : '#5258BF');
    traces = [{
      x: aggXs, y: ys, type: 'bar', marker: { color: colors },
      error_y: { type: 'data', array: errs, color: '#7C9EDE',
                 thickness: 1.5, width: 6 },
      text: ys.map(y => y.toFixed(3)), textposition: 'outside',
    }];
    layout.xaxis = { title: 'Sample', tickangle: -25,
                     gridcolor: '#E8EBF2', automargin: true };
    layout.yaxis.rangemode = 'tozero';
  } else if (state.chartKind === 'box') {
    // One box per sample, drawing values from the per-cycle distribution.
    traces = analysed.map((s, i) => ({
      y: perCycle[i],
      type: 'box',
      name: s.sample_id,
      boxpoints: 'all', jitter: 0.5, pointpos: 0,
      marker: { color: '#5258BF', size: 6,
                line: { color: '#FFFFFF', width: 1 } },
      line: { color: '#7C9EDE' },
      fillcolor: 'rgba(124, 158, 222, 0.18)',
      hovertemplate: `${s.sample_id}<br>%{y:.4f} mg/L<extra></extra>`,
    }));
    layout.xaxis = { title: 'Sample', tickangle: -25,
                     gridcolor: '#E8EBF2', automargin: true };
  } else { /* swarm */
    // Jittered scatter per sample, distribution drawn from per_cycle.
    traces = analysed.map((s, i) => {
      const ys = perCycle[i];
      // x position = i, plus small random jitter
      const xs = ys.map(() => i + (Math.random() - 0.5) * 0.35);
      return {
        x: xs, y: ys,
        mode: 'markers', type: 'scatter',
        name: s.sample_id,
        marker: { color: '#5258BF', size: 10,
                  line: { color: '#FFFFFF', width: 1.5 } },
        hovertemplate: `${s.sample_id}<br>%{y:.4f} mg/L<extra></extra>`,
      };
    });
    layout.xaxis = {
      title: 'Sample',
      tickmode: 'array',
      tickvals: analysed.map((_, i) => i),
      ticktext: aggXs,
      tickangle: -25,
      gridcolor: '#E8EBF2', automargin: true,
      range: [-0.7, analysed.length - 0.3],
    };
  }

  Plotly.newPlot('ionChart', traces, layout,
                 { displayModeBar: false, responsive: true });
  // Plotly sometimes caches a stale container width when the chart is
  // first rendered (especially right after the parent card was unhidden
  // by `runAnalysis`).  Force a resize on the next animation frame so the
  // first bar/box/swarm always fills the card width without the user
  // having to switch chart types and back.
  requestAnimationFrame(() => {
    try { Plotly.Plots.resize('ionChart'); } catch (e) { /* ignore */ }
  });
  setTimeout(() => {
    try { Plotly.Plots.resize('ionChart'); } catch (e) { /* ignore */ }
  }, 220);
  document.querySelectorAll('#ionPickerRow .ion-chip').forEach(c => {
    c.classList.toggle('active', c.textContent === ion);
  });
}

function toggleFeatures() {
  const tbl = document.getElementById('featuresTable');
  const btn = document.getElementById('featuresToggleBtn');
  if (!tbl || !btn) return;
  const hidden = tbl.classList.toggle('hidden');
  btn.textContent = hidden ? 'Show features' : 'Hide features';
}

/* -------------------------------------------------------------------------
 * Phase 5 — Save Data
 * ----------------------------------------------------------------------- */

async function preparePhase5() {
  await loadMeasuredSamples();
  renderBatchList();
  renderSaveSummary();
  renderActivityLog();
  // Reason field becomes required if any sample has an amber/red banner
  const anyReview = state.measuredSamples.some(
    s => s.banner === 'review' || s.banner === 'anomaly');
  const reqEl = document.getElementById('reasonReq');
  if (reqEl) reqEl.classList.toggle('hidden', !anyReview);
}

/* Aggregate counters for the Phase 5 "Session summary" card. */
function renderSaveSummary() {
  const el = document.getElementById('saveSummary');
  if (!el) return;
  const samples = state.measuredSamples || [];
  if (!samples.length) {
    el.innerHTML = '<div class="save-empty">No measurements yet.</div>';
    return;
  }
  const flagged  = samples.filter(s => s.banner === 'review' || s.banner === 'anomaly').length;
  const remeas   = (state.eventLog || []).filter(e => e.type === 'remeasured').length;
  const deleted  = (state.eventLog || []).filter(e => e.type === 'deleted').length;
  el.innerHTML = `
    <div class="summary-row"><span class="k">Saved samples</span><span class="v">${samples.length}</span></div>
    <div class="summary-row"><span class="k">Re-measured</span><span class="v">${remeas}</span></div>
    <div class="summary-row"><span class="k">Deleted</span><span class="v">${deleted}</span></div>
    <div class="summary-row"><span class="k">Flagged for review</span><span class="v ${flagged ? 'warn' : ''}">${flagged}</span></div>
  `;
}

/* Per-event list for the Phase 5 "Activity log" card.  Newest first.
 * We deliberately do NOT show 'analyzed' events here — analysis is a
 * derived computation, not a save action, so it would just add noise. */
function renderActivityLog() {
  const el = document.getElementById('saveLog');
  if (!el) return;
  const log = (state.eventLog || [])
    .filter(e => e.type === 'saved' || e.type === 'remeasured' || e.type === 'deleted')
    .slice().reverse();
  if (!log.length) {
    el.innerHTML = '<div class="save-empty">Nothing yet — finish a measurement first.</div>';
    return;
  }
  const ICONS = { saved: '✓', remeasured: '↻', deleted: '✕' };
  const LABELS = { saved: 'Saved', remeasured: 'Re-measured', deleted: 'Deleted' };
  const fmtTime = iso => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch (e) { return iso; }
  };
  el.innerHTML = log.map(e => `
    <div class="log-row log-${escape(e.type)}">
      <span class="log-icon">${ICONS[e.type] || '·'}</span>
      <span class="log-body">
        <strong>${escape(LABELS[e.type] || e.type)}</strong>
        — ${escape(e.sample_id || '')}
      </span>
      <span class="log-time">${escape(fmtTime(e.ts))}</span>
    </div>
  `).join('');
}

function renderBatchList() {
  const el = document.getElementById('batchList');
  if (!el) return;
  if (!state.measuredSamples.length) {
    el.innerHTML =
      '<div class="batch-empty">No measurements yet — run a sample first.</div>';
    return;
  }
  const rows = [];
  for (const s of state.measuredSamples) {
    const status = s.analyzed
      ? `<span class="banner-tag ${s.banner}">${s.banner.toUpperCase()}</span>`
      : `<span class="banner-tag pending">PENDING ANALYSIS</span>`;
    const ions = s.predictions
      ? (s.predictions.predictions || [])
          .slice(0, 6)
          .map(p => p.below_lod ? `${p.ion}<LOD`
                                : `${p.ion} ${p.value_mg_l.toFixed(3)}`)
          .join(' · ')
      : '—';
    rows.push(`
      <div class="batch-row">
        <div class="batch-row-head">
          <strong>${escape(s.sample_id)}</strong>
          ${status}
          <span class="batch-row-time">${escape(s.measured_at)}</span>
        </div>
        <div class="batch-row-meta">${s.n_samples} data points · ${escape(ions)}</div>
        <div class="batch-row-actions">
          <button class="btn-link danger" onclick="reMeasureSample('${escape(s.sample_id)}')">Re-measurement</button>
        </div>
      </div>`);
  }
  el.innerHTML = rows.join('');
}

/* Re-measure a sample.
 *
 * The sample's metadata (sample_id, donor pouch, collection date, etc.)
 * is preserved — only the CV data is discarded.  The user is taken
 * back to Phase 1 (Sample Identification) where the original data is
 * pre-filled, so they can confirm the same sample and run a fresh
 * measurement.  When the new measurement finishes, it overwrites the
 * old one (existing app logic already de-dupes by sample_id).
 */
async function reMeasureSample(sid) {
  const sample = (state.measuredSamples || []).find(s => s.sample_id === sid);
  if (!confirm(
        `Re-measure sample "${sid}"?\n\n` +
        `Its previous CV data will be deleted, then you'll go back to ` +
        `Sample Identification with the same sample info pre-filled.\n` +
        `The new measurement will replace the old one.`)) return;
  try {
    // 1. Delete the previous measurement record (server-side).  The
    //    server keeps any sample_meta we send back via /api/sample/manual.
    await fetchDelete(`/api/session/samples/${encodeURIComponent(sid)}`);
    await loadMeasuredSamples();
    renderBatchList();
    logSampleEvent('remeasured', sid);

    // 2. Pre-fill Phase 1 with the original sample metadata.
    const meta = (sample && sample.sample_meta) || {};
    state.sample = {
      sample_id:       sid,
      donor_pouch:     meta.donor_pouch || '',
      collection_date: meta.collection_date || '',
      collection_site: meta.collection_site || '',
      operator:        meta.operator || state.operator || '',
      __remeasure:     true,
    };
    // Persist to the backend so subsequent measure calls use this sample.
    const d = await postJSON('/api/sample/manual', state.sample);

    // 3. Refresh the Phase 1 confirmCard with THIS sample's data.  Without
    //    this call the card still shows whichever sample was last confirmed
    //    (e.g. the most recent one before re-measurement was clicked), so
    //    the operator sees the wrong sample on Phase 1.  Also clear the
    //    stale QR image and the inline manual-entry form for mobile so it
    //    can be re-typed against this sample.
    state.scannedQrImage = null;
    const fullMeta = (d && d.meta) || state.sample;
    renderConfirmCard(fullMeta, 'MANUAL');
    document.getElementById('btnConfirmSample').disabled = false;
    setText('hdrSampleId', sid);
    document.getElementById('hdrSample')?.classList.remove('hidden');
    // Pre-fill mobile inline form fields too so the operator sees the
    // sample's metadata even if the confirmCard is hidden by phase logic.
    const fields = {
      'inl_manSampleId':  fullMeta.sample_id || sid,
      'inl_manDonor':     fullMeta.donor_pouch || '',
      'inl_manDate':      fullMeta.collection_date || '',
      'inl_manStorage':   fullMeta.storage_location || '',
      'inl_manAliquot':   fullMeta.aliquot_purpose || '',
      'inl_manHospital':  fullMeta.hospital_site || '',
      'inl_manNotes':     fullMeta.notes || '',
    };
    for (const [id, v] of Object.entries(fields)) {
      const el = document.getElementById(id);
      if (el) el.value = v;
    }
    // Hide the inline form on mobile since confirmCard is now showing the
    // (correct) sample.  goPhase(1) will follow the same logic.
    document.getElementById('inlineManualForm')?.classList.add('hidden');

    // 4. Go to Phase 1 (Sample identification).
    goPhase(1);
    toast('Old measurement deleted — confirm sample and re-measure.', 'success');
  } catch (e) {
    toast(`Re-measurement setup failed: ${e.message || e}`, 'error');
  }
}

async function signAndSave() {
  const reason = val('signReason');
  const anyReview = state.measuredSamples.some(
    s => s.banner === 'review' || s.banner === 'anomaly');
  if (anyReview && !reason) {
    toast('Override justification required (one or more banners is amber/red).', 'error');
    return;
  }
  // Refuse to save if any measured sample hasn't been analysed yet.
  const unanalysed = state.measuredSamples.filter(s => !s.analyzed);
  if (unanalysed.length > 0) {
    if (!confirm(`${unanalysed.length} sample(s) haven't been analysed: ` +
                 unanalysed.map(s => s.sample_id).join(', ') +
                 `\n\nSave anyway? (only analysed samples produce a bundle.)`)) return;
  }
  try {
    const d = await postJSON('/api/save', { notes: val('notesArea'), reason });
    if (!d.ok) { toast(d.error || 'Save failed', 'error'); return; }
    const result = document.getElementById('saveResult');
    result.classList.remove('hidden');
    const links = (d.saved || [])
      .filter(x => x.bundle_name)
      .map(x => `<div><a href="${x.download_url}" class="btn-link" download>${escape(x.bundle_name)}</a> · banner: <span class="banner-tag ${x.banner}">${x.banner}</span></div>`)
      .join('');
    result.innerHTML =
      `<div class="banner ok" style="margin: 0;">Saved ${d.n_bundles} bundle(s) to <code>${escape(d.export_dir)}</code>.</div>` +
      `<div style="margin-top: 10px;">${links}</div>`;
    toast(`Saved ${d.n_bundles} bundle(s).`, 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function runNextSample() {
  // Keeps the operator's session intact and the per-sample measurement
  // results in memory; just wipes the "currently identified sample" so we
  // can scan the next cryovial.
  await postJSON('/api/session/reset_sample', {});
  // Clear "currently identified sample" everywhere it surfaces in the UI:
  // the confirm card, the header pill, the inline manual form (mobile),
  // and the in-memory state.sample object.  Without this, Phase 1 would
  // re-show the previous sample's metadata and leave Confirm enabled.
  state.sample = null;
  state.scannedQrImage = null;
  document.getElementById('confirmCard').classList.add('hidden');
  document.getElementById('btnConfirmSample').disabled = true;
  setText('hdrSampleId', '—');
  document.getElementById('hdrSample')?.classList.add('hidden');
  // Inline manual form (mobile): re-show empty.
  const inl = document.getElementById('inlineManualForm');
  if (inl) {
    inl.classList.remove('hidden');
    ['SampleId','Donor','Date','Storage','Aliquot','Hospital','Notes'].forEach(k => {
      const el = document.getElementById('inl_man' + k);
      if (el) el.value = '';
    });
  }
  // Phase 5 leftover state
  document.getElementById('saveResult')?.classList.add('hidden');
  document.getElementById('signReason').value = '';
  document.getElementById('notesArea').value = '';
  state.predictions = null; state.features = null; state.banner = 'ok';
  document.getElementById('predictionsCard').classList.add('hidden');
  document.getElementById('featuresCard').classList.add('hidden');
  // Reset Phase 3 to its in-progress state for the next run
  document.getElementById('phase3InProgress').classList.remove('hidden');
  document.getElementById('phase3Finished').classList.add('hidden');
  goPhase(1);
}

/* -------------------------------------------------------------------------
 * Helpers
 * ----------------------------------------------------------------------- */

function val(id) { return (document.getElementById(id).value || '').trim(); }
function chk(id) { return document.getElementById(id).checked; }
function setText(id, s) { const el = document.getElementById(id); if (el) el.textContent = s; }
function escape(s) { return String(s ?? '').replace(/[<>&"']/g, c =>
  ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c])); }

async function getJSON(url) { const r = await fetch(url); return r.json(); }
async function postJSON(url, body) {
  const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  return r.json();
}
async function fetchDelete(url, body) {
  const opts = { method: 'DELETE' };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  return fetch(url, opts).then(r => r.json());
}

function toggleHelp() { document.getElementById('helpPanel').classList.toggle('open'); }

function toast(text, kind) {
  const t = document.getElementById('toast');
  t.textContent = text;
  t.className = 'toast show ' + (kind || '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = 'toast'; }, 2400);
}
