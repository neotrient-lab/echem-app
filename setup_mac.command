#!/bin/bash
# ============================================================
# Neotrient Electrochemical App — One-time setup script (macOS)
#
# WHAT THIS DOES:
#   1. Checks that Python 3.11 (or newer) is installed
#   2. Creates a private Python "virtual environment" inside this folder
#   3. Installs all the libraries the app needs (Flask, matplotlib, etc.)
#
# HOW TO USE:
#   Just DOUBLE-CLICK this file in Finder. A Terminal window will pop up
#   and run the setup automatically. When you see "ALL DONE!" you can
#   close the window and start the app with start_app.command.
#
#   You only need to run this ONCE per computer.
# ============================================================

# Move into the folder where this script lives so the venv is created
# alongside the app code, not in the user's home folder.
cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "   Neotrient Electrochemical App — First-time setup"
echo "============================================================"
echo ""

# --- Step 1: Find a usable Python ---------------------------------------
echo "[1/3] Looking for Python 3.11 or newer ..."

PYTHON=""
for candidate in python3.13 python3.12 python3.11 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
        if [ -n "$version" ]; then
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
                PYTHON="$candidate"
                echo "      Found: $candidate (Python $version)"
                break
            fi
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ERROR: No suitable Python was found."
    echo ""
    echo "  Please install Python 3.11 (or newer) before running this script:"
    echo "    1. Open https://www.python.org/downloads/"
    echo "    2. Download the latest installer for macOS"
    echo "    3. Double-click the .pkg file and follow the prompts"
    echo "    4. Run this setup script again"
    echo ""
    read -p "Press Return to close this window ..."
    exit 1
fi

# --- Step 2: Create the virtual environment -----------------------------
echo ""
echo "[2/3] Creating private Python environment in .venv/ ..."

if [ -d ".venv" ]; then
    echo "      .venv already exists — removing the old one and starting fresh"
    rm -rf .venv
fi

"$PYTHON" -m venv .venv
if [ ! -f ".venv/bin/python" ]; then
    echo "  ERROR: Could not create the virtual environment."
    read -p "Press Return to close this window ..."
    exit 1
fi
echo "      Done."

# --- Step 3: Install dependencies ---------------------------------------
echo ""
echo "[3/3] Installing libraries (this can take 1-2 minutes) ..."
echo ""

.venv/bin/pip install --upgrade pip --quiet
if [ -f "echem_app/requirements.txt" ]; then
    .venv/bin/pip install -r echem_app/requirements.txt
elif [ -f "requirements.txt" ]; then
    .venv/bin/pip install -r requirements.txt
else
    echo "  ERROR: requirements.txt not found."
    read -p "Press Return to close this window ..."
    exit 1
fi

echo ""
echo "============================================================"
echo "   ALL DONE! Setup completed successfully."
echo "============================================================"
echo ""
echo "   To start the app:"
echo "     -> Close this window"
echo "     -> Double-click  start_app.command  in Finder"
echo ""
read -p "Press Return to close this window ..."
