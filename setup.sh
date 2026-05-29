#!/usr/bin/env bash
# One-time setup to run the bot on a Lightning AI Studio (or any Linux box).
#   bash setup.sh
set -e

echo "==> Installing ffmpeg (if missing)..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v conda >/dev/null 2>&1; then
    conda install -y -c conda-forge ffmpeg
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y ffmpeg
  else
    echo "!! Could not auto-install ffmpeg. Install it manually, then re-run."
    exit 1
  fi
fi
ffmpeg -version | head -1

echo "==> Installing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "Setup complete."
echo "Next:"
echo "  1) Put your .env (with API keys) in this folder."
echo "  2) Generate a batch:   python -m socialbot.cli generate --count 2"
echo "  3) Review dashboard:   python -m socialbot.cli serve --host 0.0.0.0 --port 8000"
echo "     (on Lightning, use the Studio's port-forward/share to get a URL)"
