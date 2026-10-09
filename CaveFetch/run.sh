#!/usr/bin/env bash
set -e
echo "========================================================"
echo "Starting CaveFetch - e621 Browser & Downloader"
echo "========================================================"

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 could not be found."
    echo "Please install Python 3 (https://www.python.org/downloads/)."
    exit 1
fi

echo "Verifying dependencies..."
python3 -m pip install -q -r requirements.txt || python3 -m pip install --user -q -r requirements.txt

echo "Launching CaveFetch..."
python3 run.py
