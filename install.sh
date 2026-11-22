#!/bin/sh
# Pin Palette installer for Linux and macOS.
#   curl -fsSL https://raw.githubusercontent.com/IamOumarIbrahim/pin-palette/main/install.sh | sh
set -e

REPO="IamOumarIbrahim/pin-palette"

# Use this folder if the script runs from a checkout; otherwise download to ~/pin-palette.
if [ -f "$0" ] && [ -f "$(dirname "$0")/app.py" ]; then
  DIR="$(cd "$(dirname "$0")" && pwd)"
else
  DIR="${PIN_PALETTE_DIR:-$HOME/pin-palette}"
  echo "Downloading Pin Palette to $DIR"
  mkdir -p "$DIR"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$DIR" --strip-components=1
fi
cd "$DIR"

# Find Python 3.10 or newer.
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is required."
  if [ "$(uname)" = "Darwin" ]; then echo "Install it from https://www.python.org/downloads/ or run: brew install python"
  else echo "Install it with your package manager, for example: sudo apt install python3 python3-venv"; fi
  exit 1
fi

echo "Installing dependencies"
"$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
chmod +x start.sh findsimilar

# `findsimilar [image]` command: opens straight into Similar image mode.
mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/findsimilar" "$HOME/.local/bin/findsimilar"

# App launcher.
if [ "$(uname)" = "Darwin" ]; then
  L="$HOME/Desktop/Pin Palette.command"
  printf '#!/bin/sh\nexec "%s/start.sh"\n' "$DIR" > "$L" && chmod +x "$L"
  echo "Added 'Pin Palette' to your Desktop."
else
  A="$HOME/.local/share/applications"
  mkdir -p "$A"
  cat > "$A/pin-palette.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pin Palette
Comment=Find Pinterest images with your color palette
Exec=$DIR/start.sh
Terminal=true
Categories=Graphics;
EOF
  cat > "$A/findsimilar.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Find Similar
Comment=Find images that look like yours
Exec="$DIR/findsimilar" %f
Icon=image-x-generic
MimeType=image/jpeg;image/png;image/webp;image/gif;image/bmp;
Terminal=false
Categories=Graphics;
EOF
  echo "Added 'Pin Palette' and 'Find Similar' to your app menu."
fi

echo "Done. Starting Pin Palette..."
exec ./start.sh
