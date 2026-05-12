# Neotrient Echem App — Operator Usage Guide (v0.2.0)

This guide assumes the app is already installed and you can open
`http://127.0.0.1:8080` in a browser.  For installation see
[`INSTALL.md`](./INSTALL.md).

## Contents

1. [Two run modes — Single vs Sequence](#two-run-modes)
2. [Two sample-identification forms — Clinical vs Standard](#sample-id-forms)
3. [Recipe — Single CV on a clinical sample](#recipe-single-cv-clinical)
4. [Recipe — Single SWV on a standard solution](#recipe-single-swv-standard)
5. [Recipe — Sequence run with mixed batches](#recipe-sequence)
6. [Managing presets](#managing-presets)
7. [Output files](#output-files)
8. [Cancel, re-measure, end sequence](#cancel-remeasure-end)
9. [Troubleshooting](#troubleshooting)

---

## <a name="two-run-modes"></a>1. Two run modes — Single vs Sequence

Pick one in **Configuration → Run mode**:

| Mode | When to use | Workflow |
|---|---|---|
| **Single** | One sample at a time. Walk-in lab, calibration, single QC. | Confirm sample → measure → save → next sample. |
| **Sequence** | Plan many samples up front. Daily QC battery, multi-standard calibration curve, batch of donor samples. | Build batches → fill all sample info → confirm sequence → measure each in order. |

Single mode is the v0.1.x behaviour, unchanged.

Sequence mode adds an extra **Create Sequence** phase before Sample
Identification.  Each batch in a sequence can use a different method
preset (e.g. CV for sample 1-3, SWV for sample 4-5).

---

## <a name="sample-id-forms"></a>2. Two sample-identification forms

Pick one in **Configuration → Sample identification → Sample category**:

| Category | Form fields |
|---|---|
| **Clinical sample** | Sample ID, Donor pouch, Collection date, Storage location, Aliquot purpose, Clinical site, Notes |
| **Standard solution** | Sample ID, **Sample type** (Standard / Blank / Sample / QC), Concentration value+unit, Prep date, Lot / batch, Solvent / matrix, Notes |

The chosen form appears at Sample Identification (Manual entry).
QR scanning works in both — the QR payload is matched against the
relevant fields.

> ⚠️ **Note:** "Sample category" (Configuration) is different from
> "Sample type" (Manual-entry → Standard form).  Category picks the
> FORM layout; type classifies a specific standard solution.

---

## <a name="recipe-single-cv-clinical"></a>3. Recipe — Single CV on a clinical sample

1. Open the app.
2. **Configuration**:
   - Run mode: **Single**
   - Sample category: **Clinical sample**
   - Operator: your name
   - Device: pick the EmStat4T from the dropdown (USB or BLE)
   - Preset: pick a CV preset (e.g. `preset-1` or `method-2`)
3. Click **Connect** → wait for "Connected" indicator.
4. Click **Begin session**.
5. **Sample Identification** — scan the QR on the cryovial, *or*
   click **Manual entry** and fill the form.  Click **Confirm sample**.
6. **Sample Loading** — drop the sample onto the electrode.
   - If preset is *drop-detect*: the chip auto-starts once it sees the
     drop.
   - If preset is *manual*: click **Start measurement**.
7. **Measurement** — watch the live voltammogram.  Cycles appear as
   coloured traces.  Wait for "Completed".
8. **Analysis** — pick a model, tick which samples + cycles to feed
   it, click **Run analysis**.  Concentration prediction + extracted
   features appear.
9. **Finalize** — review the per-session log, optionally add notes,
   sign off, click **Sign and save**.  Exported `.zip` bundle path is
   shown.
10. Click **Run next sample** to repeat for another sample (session
    stays open), or **Done — end session** to fully close.

---

## <a name="recipe-single-swv-standard"></a>4. Recipe — Single SWV on a standard solution

Same as Single CV (above), with these differences:

1. **Configuration**:
   - Sample category: **Standard solution**
   - Preset: **`preset-swv-1`** (or a custom SWV preset — see
     [Managing presets](#managing-presets) below)
2. **Sample Identification** — Manual entry form shows the
   *Standard solution* fields (Sample type, Concentration, Prep date,
   Lot, Solvent).  Prep date defaults to today.
3. **Measurement** — live voltammogram shows **three traces**:
   - **I forward** (indigo) — current sampled at end of forward pulse
   - **I reverse** (orange) — current at end of reverse pulse
   - **I difference** (green) — forward minus reverse (the typical
     SWV signal you'd publish)

The deposition step (if `t_dep > 0` in the preset) runs before the
SWV sweep — the chip holds at `E_dep` for `t_dep` seconds, sampling
every 200 ms.  The status panel shows "Pre-treatment running…" during
this phase.

---

## <a name="recipe-sequence"></a>5. Recipe — Sequence run with mixed batches

Use Sequence mode when you have a known list of samples ahead of
time — e.g. a calibration curve (5 standards) followed by 3 patient
samples.

1. **Configuration** — same as above, but Run mode = **Sequence**.
2. Click **Begin session** → lands on **Create Sequence** (Phase 2).
3. **Create a batch:**
   - Batch ID: e.g. `B001` (or accept the auto-suggested value)
   - Sample name (prefix): e.g. `STD` — samples will be
     auto-numbered `STD_1`, `STD_2`, …
   - Method preset: pick which preset this batch uses (e.g.
     `preset-swv-1` for SWV calibration)
   - Click **Create batch**.
4. **Add samples to the batch:**
   - Click **Add sample** → the app jumps to Sample Identification
     with the sample's auto-name pre-filled.
   - Click **Manual entry**, fill in concentration / type / lot, then
     **Confirm sample**.  You return to Create Sequence with the row
     populated.
   - Repeat for sample 2, 3, etc.  Each row shows: name, type, conc.,
     date, operator, method.
5. **Create another batch** (optional) — e.g. `B002` with a different
   preset (`preset-1` CV for clinical samples).  Add samples the same
   way.
6. When the plan is complete, click **Confirm** at the bottom of
   Create Sequence.  The button is enabled only when every batch has
   at least one sample and every sample has filled info.
7. **Sample Identification** for sample 1 of B001 appears with the
   info already in the confirm card.  A **Sequence overview** card at
   the top shows the full plan with sample 1 highlighted.  Click
   **Confirm sample** to advance.
8. Measure as usual (Sample Loading → Measurement → Analysis →
   Finalize).
9. After **Sign and save**, click **Run next sample** — the app loads
   sample 2 automatically (and switches the session preset if the
   next batch uses a different one).
10. When you reach the **last** sample, the button label changes to
    **End sequence**.  Clicking it prompts:
    > End sequence?
    > No more samples will be measured.
    Confirm → goes straight to Finalize (skipping any further loops).

---

## <a name="managing-presets"></a>6. Managing presets

Open **Configuration → Experiment preset → Manage presets** (link
inside the preset card).  The modal lists every preset; click one to
load its values into the editor at the bottom.

### Creating a new preset

1. Fill **Preset name** + **Technique** (CV or SWV).  The visible
   fields swap based on technique.
2. Fill the parameters appropriate to your technique:
   - **CV:** E vertex 1 / E vertex 2 / Scan rate / Cycles (n_scans)
   - **SWV:** E end / E amplitude / Frequency / Deposition E / Deposition time
3. Common: E begin / E step / Pretreat duration / current range
   picker.
4. Tick **Set as default** if you want this to be the next
   session's default preset.
5. Click **Add preset**.

### Protected presets (`preset-1`, `preset-swv-1`, `Dummy cell test`)

These were calibrated against PSTrace and shipped with the app.
They are flagged `protected: true` in `presets.json`.  Deleting any
of them requires the admin password (`NEO-001`, documented in
`PASSWORDS.txt`).  Editing them does not require a password.

If you accidentally edit a protected preset, you can either revert
the edit by re-entering known values, or delete + reinstall the app
folder.

---

## <a name="output-files"></a>7. Output files

After each measurement the app writes three files to:

```
~/Documents/Neotrient/results/<YYYY-MM-DD>/
```

| File | Contents |
|---|---|
| `HHMMSS_<sample_id>.csv` | Self-describing CSV.  Header is a `# key=value` block (experiment, operator, device, sample, every preset parameter, status, notes), then the data columns. |
| `HHMMSS_<sample_id>.json` | Same metadata as a JSON sidecar (kept for backwards compatibility with downstream tools). |
| `log.txt` | Append-only daily log: one line per measurement / event. |

### CSV header example (CV)

```
# experiment=CV
# operator=neo-001
# device_id=ES4T-01
# sample_id=DI-1
# generated_at=2026-05-08T10:23:45.123456
# e_begin=1.0
# e_vtx1=-1.0
# e_vtx2=1.0
# e_step=0.01
# scan_rate=0.05
# n_scans=5
# ...
# status=ok
# notes=
index,scan,potential_V,current_A,status,current_range
0,0,0.99,8.66e-04,,1mA
...
```

### CSV header example (SWV)

```
# experiment=SWV
# operator=neo-001
# device_id=ES4T-01
# sample_id=STD-Fe-1mM
# e_begin=-1.0
# e_end=1.0
# e_amplitude=0.025
# frequency_hz=25
# e_dep=-1.0
# t_dep=60
# ...
index,potential_V,current_fwd_A,current_rev_A,current_diff_A,status,current_range
0,-1.0,5.1e-07,-2.3e-07,7.4e-07,,10uA
...
```

### Export bundle (Finalize phase)

When you click **Sign and save** at Finalize, the app zips together
all per-sample files for the session into one bundle, alongside an
`audit.log` and the AI inference output.  Bundle path is shown after
save.

---

## <a name="cancel-remeasure-end"></a>8. Cancel, re-measure, end sequence

| Action | When to use | What it does |
|---|---|---|
| **Cancel measurement** (Phase 3) | Drop didn't take, noisy data, wrong sample | Aborts current chip script, returns to Sample Loading. |
| **Reset sample** (Phase 1) | Wrong sample identified | Clears the confirm card, restarts QR scanner. |
| **Re-measure** (Analysis page) | Want to redo a previously-measured sample | Deletes the old measurement, returns to Sample Identification with original info pre-filled. |
| **End sequence** (Phase 3-5, last sample) | Want to skip remaining samples and finalize | Prompts for confirmation; on OK skips straight to Finalize. |
| **Cancel session** (header button) | Discard everything and start over | Wipes ALL state (samples, sequence plan, operator, presets selection) and returns to Configuration as if the app just launched. |

> ⚠️ During an active measurement (Phase 3), the **Cancel session**
> button in the header is **disabled**.  You must click **Cancel
> measurement** first.  This prevents accidental loss of a running
> chip session.

---

## <a name="troubleshooting"></a>9. Troubleshooting

### "Sample type" is missing from the Sample identified card

That field was added in v0.2.0.  Make sure you're running v0.2.0
(check the sidebar: "LAB ANALYSIS · v0.2.0").  Hard-reload the
browser (Cmd+Shift+R) after the app restarts.

### Live voltammogram stays empty during SWV measurement

The chart was probably initialised for the wrong technique.  The app
should auto-recover (it re-inits when the chip reports the technique
via the `drop_armed` event), but if it doesn't:

1. Click **Cancel measurement** → returns to Phase 2.
2. Click **Start measurement** again — the chart re-initialises.

If this keeps happening, check the browser console (F12) for a
"SWV chart not initialised" warning — that's the defensive guard
catching a mismatched state.

### "Sequence is not work" after running next sample

Make sure you confirmed the previous sample at **Sign and save**
(not just clicked **Run next sample** prematurely).  The sequence
cursor advances only after a successful save.

If the sequence overview card shows the wrong sample highlighted,
click that sample's row to verify.

### Bluetooth (PS-539B) connection takes forever

First pairing on macOS can take 30-60 seconds.  Subsequent connections
are fast.  If it never connects:

1. **System Settings → Bluetooth** → find the PalmSens device,
   right-click → **Forget Device**.
2. Power-cycle the EmStat4T.
3. Re-pair from System Settings.
4. Try **Connect** in the app again.

### Other problems

Send a screenshot of the error + the Terminal window where the app is
running.  The Terminal often contains the exact clue we need.

---

## Where the app keeps your data

All measurement files are saved automatically inside:

```
~/Documents/Neotrient/results/<YYYY-MM-DD>/
```

These are plain CSV + JSON + log files.  You can open them in Excel,
Numbers, or any text editor.  Backing them up is just a matter of
copying the `results/` folder to a USB stick or a cloud drive.

The app **does not** send any of your data to the internet.
Everything stays on your computer.

---

## Privacy & internet

This app:

- Runs **entirely on your own computer**.
- Does **not** send measurement data anywhere.
- Does need an internet connection **once** during install (to
  download Python and Python libraries).
- After that, you can use it fully offline.

---

## Getting help

If anything in this guide doesn't work as described, send a
screenshot + the version string from the sidebar ("LAB ANALYSIS ·
v0.2.0") to the team.  We'll reply quickly.
