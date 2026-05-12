# Neotrient Electrochemical App — Install & Use Guide

Welcome! This guide walks you through installing and running the
Neotrient Cyclic Voltammetry app on your own computer. **No prior
programming or terminal experience is needed.** Each step takes
1–5 minutes and most of them are one-time only.

If you get stuck on any step, please send a screenshot to the team
and we will help.

---

## What you will need

1. **A laptop or desktop computer** running macOS (10.15 Catalina or
   newer) or Windows 10/11.
2. **A PalmSens EmStat4T potentiostat** with a USB cable, *or*
   a Bluetooth-equipped EmStat4T (model PS-539B).
3. **About 15 minutes** for the first-time install.

That's it. You do **not** need to know Python, git, or terminal
commands — you will only need to double-click two files.

---

## Step 1 — Install Python (one-time, ~5 min)

The app is written in Python, so your computer needs Python first.

### On macOS

1. Open the website **<https://www.python.org/downloads/>** in any
   browser (Safari, Chrome, etc.).
2. Click the big yellow button **Download Python 3.x.x** (any version
   3.11 or newer is fine).
3. Once the file finishes downloading, open your **Downloads** folder
   and **double-click the file** that ends in `.pkg`
   (for example `python-3.13.0-macos11.pkg`).
4. Click **Continue → Continue → Continue → Install**, type your Mac
   password when asked, and wait for the installer to finish.
5. Close the installer window.

That's it for macOS. You won't need to touch Python directly again.

### On Windows

1. Open **<https://www.python.org/downloads/>** in any browser.
2. Click the big yellow button **Download Python 3.x.x**.
3. Open your **Downloads** folder and **double-click the file**
   that ends in `.exe`.
4. **VERY IMPORTANT:** on the very first screen of the installer,
   tick the checkbox at the bottom that says
   **"Add Python to PATH"**. If you forget this step, the app won't
   start later.
5. Click **Install Now**, wait for it to finish, then click **Close**.

That's it.

---

## Step 2 — Download the app from GitHub (one-time, ~2 min)

Your team lead will send you a link that looks like
`https://github.com/<owner>/<repo>` and an invitation to access it.

1. Open the link in your browser.
2. If GitHub asks you to **sign in**, do so with your GitHub account.
   (If you don't have one, create a free account at
   <https://github.com/signup>.)
3. If GitHub asks you to **accept an invitation** to the repository,
   click **Accept invitation**.
4. On the repository page, look for the green **<> Code** button on
   the upper right. Click it.
5. In the dropdown menu, click **Download ZIP** at the bottom.
6. Open your **Downloads** folder. You should see a file like
   `<repo-name>-main.zip`.
7. **Double-click** the ZIP file to unzip it. You will get a folder
   like `<repo-name>-main/`.
8. **Move that folder to a permanent location** that's easy to find,
   for example your **Desktop** or your **Documents** folder.
   (Don't leave it in Downloads — your computer might delete old
   downloads automatically.)

> **Tip:** rename the unzipped folder to something simple like
> `Neotrient` so it's easier to find later.

---

## Step 3 — Run the one-time setup (one-time, ~3 min)

Now we install the libraries the app needs. **Just one double-click.**

### On macOS

1. Open the folder you unzipped in Step 2.
2. Find the file named **`setup_mac.command`**.
3. **Double-click it.**

A black Terminal window will pop up. You will see:

```
============================================================
   Neotrient Electrochemical App — First-time setup
============================================================
[1/3] Looking for Python 3.11 or newer ...
       Found: python3.13 (Python 3.13)
[2/3] Creating private Python environment in .venv/ ...
[3/3] Installing libraries (this can take 1-2 minutes) ...
       ...lots of "Successfully installed" lines...
============================================================
   ALL DONE! Setup completed successfully.
============================================================
   Press Return to close this window ...
```

Wait until you see **ALL DONE!** Then press the **Return** key
to close the window.

> **First-time security warning?** macOS may show a popup that says
> something like *"`setup_mac.command` cannot be opened because it is
> from an unidentified developer."*
>
> If this happens:
> 1. Click **OK / Cancel** on the popup.
> 2. Open **System Settings → Privacy & Security**.
> 3. Scroll down to the **Security** section. You will see a line
>    saying *"`setup_mac.command` was blocked..."* with an
>    **Open Anyway** button. Click it.
> 4. A second popup appears — click **Open** to confirm.
>
> You only need to do this the first time.

### On Windows

1. Open the folder you unzipped in Step 2.
2. Find the file named **`setup_windows.bat`**.
3. **Double-click it.**

A black command window will pop up and run the same steps as the
macOS version. Wait for **ALL DONE!**, then press any key to close.

> **First-time SmartScreen warning?** Windows may show a blue popup
> saying *"Windows protected your PC."*
>
> If this happens, click **More info**, then click the
> **Run anyway** button that appears.

---

## Step 4 — Connect the EmStat4T

### Using USB (simplest — recommended for first use)

1. Plug the EmStat4T into your computer with a USB cable.
2. Wait 5 seconds for your computer to recognise the device.

That's it.

### Using Bluetooth (model PS-539B only)

1. Power on the EmStat4T (it should blink blue when ready to pair).
2. On macOS: open **System Settings → Bluetooth**. Find
   `PalmSens PS-539B` (or similar) in the list of nearby devices and
   click **Connect**. The first connection can take up to a minute.
3. The light should switch from blinking to solid blue once paired.

You can leave the device paired — you don't need to repeat this
each time.

---

## Step 5 — Start the app (do this every time you want to use it)

### On macOS

1. Open the folder where you put the app.
2. **Double-click** the file named **`start_app.command`**.

A Terminal window opens and shows:

```
   Starting Neotrient Electrochemical App ...
   The app will appear at:  http://127.0.0.1:8080
   Your browser should open automatically.
```

Within 5 seconds, your default browser (Safari / Chrome / etc.)
should open the app automatically.

### On Windows

1. Open the folder where you put the app.
2. **Double-click** the file named **`start_app.bat`**.

Same as macOS — a command window opens, then your browser opens
the app at `http://127.0.0.1:8080`.

> **Browser didn't open by itself?** No problem — just open any
> browser yourself and type this address into the address bar:
>
> ```
> http://127.0.0.1:8080
> ```
>
> Then press Return.

---

## Step 6 — Use the app

The app is a wizard along the left side of the screen.  In **v0.2.0**
the workflow can take two shapes:

**Single mode (6 phases)** — one sample at a time.  Same flow as
v0.1.x:

1. **Configuration** — Operator, Device, Preset, Sample category
   (Clinical sample / Standard solution), Run mode (Single).
2. **Sample Identification** — Scan QR or use Manual entry, then
   click **Confirm sample**.
3. **Sample Loading** — Drop the sample onto the electrode (or click
   manual-trigger Start, depending on the preset).
4. **Measurement** — Live voltammogram.  CV shows per-cycle traces;
   SWV shows three traces (forward / reverse / difference).
5. **Analysis** — AI inference (if a model is loaded) + per-sample
   feature table.
6. **Finalize** — Operator sign-off + export bundle.

**Sequence mode (7 phases)** — plan a batch of samples then run
them in order.  Adds one phase up front:

1. **Configuration** — Same as Single, but pick *Run mode = Sequence*.
2. **Create Sequence** — Build one or more batches, each with a
   method preset.  Fill sample info for every sample.  Click
   **Confirm** to start the run.
3. **Sample Identification** — Verify sample 1's info that was
   pre-loaded from the batch.
4. **Sample Loading** — Drop sample 1.
5. **Measurement** — Measure sample 1.
6. **Analysis** — Review (or skip).
7. **Finalize** — Either click **Run next sample** to loop back to
   step 3 for sample 2, or click **End sequence** on the last sample
   to skip remaining samples and finalize the session.

For detailed workflow recipes (CV vs SWV runs, building a sequence
plan, switching presets per batch, recovering from a failed
measurement), see **USAGE.md** in the same folder.

> **Tip:** the workflow sidebar shows your current step in purple.
> Past steps go green; future steps stay grey until you reach them.
> Click any **green** step to revisit it; future steps are locked.

You can also browse all past measurements on the **Analysis**
page.

---

## Step 7 — Stop the app

When you finish for the day:

1. Find the Terminal / command window that opened in Step 5.
2. Click on it to bring it to the front.
3. Press **Control + C**  (the *Control* key — not Command,
   even on macOS — and the letter `C`).
4. The app will shut down. You can now close the window.

It is fine to stop / start the app many times a day.

---

## Updating to a new version

When the team lead announces a new release:

1. Open the GitHub repository link again in your browser.
2. Click the green **<> Code** button → **Download ZIP**.
3. Unzip the new file.
4. **Save your old measurement data first!**  Open the old folder
   and copy the entire `echem_app/exports/` subfolder somewhere
   safe (your Documents folder, for example).
5. Replace the old app folder with the new one.
6. Move your saved `exports/` back into the new folder if you want
   the in-app Analysis page to find your historical measurements.
7. Run `setup_mac.command` (or `setup_windows.bat`) once on the
   new folder — this updates the libraries.
8. Use `start_app.command` / `start_app.bat` as before.

---

## Common problems

### "Python is not installed" error in the setup window

→ Go back to **Step 1** and install Python. On Windows, make sure
you ticked **"Add Python to PATH"** during the installer.

### "Could not create the virtual environment" error

→ This usually means Python was installed correctly but doesn't have
permission to write to the folder. Move the app folder to a simpler
location — your **Desktop** is the easiest. Try setup again.

### The app starts but the browser doesn't open

→ Open any browser and type this address yourself:
`http://127.0.0.1:8080`

### "Address already in use" error when starting

→ The app is already running in another window. Find that window
and stop it (Control + C), or restart your computer.

### Bluetooth connection fails / takes forever

→ The first BLE pairing on macOS can take up to 60 seconds. After
that, reconnections are fast. If it never connects:

1. Open **System Settings → Bluetooth**.
2. Right-click the device → **Forget Device**.
3. Power-cycle the EmStat4T (turn off, wait 10 sec, turn on).
4. Try again.

### The app says "Device not detected" but the EmStat4T is plugged in

→ On macOS, the first time you plug in the EmStat4T, the system may
prompt you to allow access to the USB serial port. Check the
notification area and approve it, then click **Connect** in the
app again.

### I can't see anything in the Analysis page

→ The Analysis page only shows measurements that have already been
saved (via Phase 5 → **Done**). If you stopped the app mid-session
without finishing Phase 5, that measurement isn't saved. Run a few
full sessions and they will appear.

### Other problems

→ Take a **screenshot** of the error and the Terminal window, and
send it to the team. The Terminal output usually contains the exact
clue we need.

---

## Where the app keeps your data

All measurement files are saved automatically inside:

```
<your app folder>/echem_app/exports/<date>/sample_<id>_<timestamp>.csv
```

These are plain CSV files — you can open them in Excel, Numbers,
or any text editor. Backing them up is just a matter of copying
the `exports/` folder to a USB stick or a cloud drive.

The app **does not** send any of your data to the internet.
Everything stays on your computer.

---

## Privacy & internet

This app:

- Runs **entirely on your own computer**.
- Does **not** send measurement data anywhere.
- Does need an internet connection **once** during Step 1 (to
  download Python) and Step 3 (to download the Python libraries).
- After that, you can use it fully offline.

---

## Getting help

If anything in this guide doesn't work as described:

1. Take a screenshot of what you see.
2. Note which step you were on and what error appeared.
3. Send both to the team.

We will reply quickly. Welcome aboard!
