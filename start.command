#!/bin/sh
# Notion to Anki: start the app on macOS or Linux.
#   In Terminal, type "sh " (with a space), drag this file into the window and press Enter.
#   (If the file is executable, double-clicking it in Finder works too.)
# The first start creates a private Python environment in the .venv folder and
# installs the packages listed in requirements.txt (a few minutes, needs internet once).
cd "$(dirname "$0")" || exit 1

fail() {
    echo
    echo "The setup did not finish (see the messages above). Check the internet connection and try again."
    echo "If it keeps failing, delete the .venv folder and run this file again."
    exit 1
}

if [ ! -x .venv/bin/python ]; then
    PYTHON=""
    for candidate in python3 python; do
        if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
    if [ -z "$PYTHON" ]; then
        echo "Python 3.11 or newer is needed and was not found on this computer."
        echo "Install it from https://www.python.org/downloads/ and then run this file again."
        exit 1
    fi
    echo "Setting up for the first start. This takes a few minutes..."
    "$PYTHON" -m venv .venv || fail
fi

# Install again only when requirements.txt changed since the last successful install.
if ! cmp -s requirements.txt .venv/installed-requirements.txt; then
    echo "Installing the required packages..."
    .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt || fail
    cp requirements.txt .venv/installed-requirements.txt
fi

echo
echo "Starting Notion to Anki. Your browser opens in a moment."
echo "Keep this window open while you use the app; press Ctrl+C here to stop it."
echo
exec .venv/bin/python run.py "$@"
