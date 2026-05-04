#!/bin/bash
# ============================================================
# Neotrient Electrochemical App — Start the app (macOS)
#
# WHAT THIS DOES:
#   Starts the web app and opens your default browser at it.
#
# HOW TO USE:
#   Just DOUBLE-CLICK this file in Finder. A Terminal window will open
#   and the app will start. Your browser should pop up automatically
#   showing the app at  http://127.0.0.1:8080
#
#   To STOP the app:  click on this Terminal window, then press
#                     Control + C  (the Control key, not Command).
#                     Then close the window.
#
#   PREREQUISITES:    you must have run setup_mac.command at least once.
# ============================================================

cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "   Starting Neotrient Electrochemical App ..."
echo "============================================================"
echo ""

# Sanity check: did the user run setup first?
if [ ! -f ".venv/bin/python" ]; then
    echo "  ERROR: The Python environment hasn't been set up yet."
    echo ""
    echo "  Please double-click  setup_mac.command  first to install the"
    echo "  app, then come back and run start_app.command."
    echo ""
    read -p "Press Return to close this window ..."
    exit 1
fi

# Launch the app on port 8080, listen on all network interfaces so phones
# / tablets on the same WiFi can reach it, and publish on mDNS so the
# operator can browse to neotrient.local instead of an IP.
export ECHEM_HOST=0.0.0.0
export ECHEM_PORT=8080
export ECHEM_MDNS=1

echo "   The app will appear at:  http://127.0.0.1:8080"
echo "   Your browser should open automatically."
echo ""
echo "   To STOP the app:  press  Control + C  in this window,"
echo "                     then close the window."
echo ""

.venv/bin/python -m echem_app.app
