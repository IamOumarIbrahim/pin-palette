# Pin Palette

[![tests](https://github.com/IamOumarIbrahim/pin-palette/actions/workflows/tests.yml/badge.svg)](https://github.com/IamOumarIbrahim/pin-palette/actions/workflows/tests.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Windows | macOS | Linux](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Find and download Pinterest images that match your color palette.** Drop in a photo, a moodboard or a whole folder. Pin Palette extracts its color palette, searches Pinterest, and shows only the pins with the same colors in the same proportions. Pick the ones you like and download them in full resolution.

**Similar image mode** goes further: drop in one photo and get images that look like it, with the same subject, pose, framing and colors. Choose how many, tune every setting, and save them to a folder.

Useful for moodboards, brand and interior color schemes, outfit inspiration, wallpaper hunting and aesthetic collections.

![Pin Palette matching Pinterest pins to a white, silver and beige palette](docs/screenshot.png)

## Features

- **Palette extraction** from one image or a folder (up to 8 colors, with how much of the image each one covers)
- **Editable palette**: drag to change color amounts, change or remove colors, add your own, or start from scratch with no image
- **Match strictness slider**, plus fine-tune controls for color tolerance, amount tolerance and number of colors
- **Live filtering**: results re-sort instantly as you move a slider or edit the palette
- **Bulk download** of full-size originals
- **One-line install** on Windows, macOS and Linux; runs locally in your browser, no account needed
- Optional Pinterest login for more results
- **Similar image mode**: finds images that look like one photo of yours. Two local AI models do the judging: DINOv2 for look (lighting, pose, composition) and CLIP for content (subject, style). Color matching judges palette and mood. Drawings, shop listings, wrong framing, the wrong hair color, glasses and duplicates are filtered out. Every search and ranking setting has a control, and the ranking sliders re-sort instantly

## Install

**Windows**: open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/IamOumarIbrahim/pin-palette/main/install.ps1 | iex
```

**macOS / Linux**: open a terminal and run:

```sh
curl -fsSL https://raw.githubusercontent.com/IamOumarIbrahim/pin-palette/main/install.sh | sh
```

The installer downloads the app to `~/pin-palette`, installs Python if needed (Windows, via winget), adds a **Pin Palette** shortcut, and opens it in your browser.

Already downloaded the ZIP? Double-click `Start.bat` (Windows) or run `./start.sh` (macOS/Linux).

## Use

1. Drop an image or a folder onto the page, or choose **Pick colors yourself**.
2. Edit the **Palette** if you like:
   - drag the dividers in the color bar, or type a %, to change how much of each color you want
   - click a swatch or type a hex code to change a color
   - remove a color with ×, or add one with **+ Add color**
   - **Reset to images** brings back the colors read from your images
3. Press **Search**. The search words are filled in from your colors; add a subject like "bedroom" or "outfit" for tighter results.
4. Set **Match** strictness. Right is stricter. Open **Fine-tune** to set color tolerance, amount tolerance and number of colors separately. Results update instantly, also when you edit the palette.
5. Click pins to select them, then **Download**. Files go to `Pictures/Pin Palette`.

## Similar image mode

Click **Similar image** at the top, or run `findsimilar photo.jpg` (Linux/macOS; the installer adds the command).

1. Drop an image, choose one, or paste its path.
2. Press **Search**. Bing's visual search describes your image and supplies candidates. Pin Palette downloads them all in parallel and ranks them by how close they look. Results show up while it downloads.
3. Tune the **Ranking** sliders; results re-sort instantly, with no new download:
   - **Look**, **Content**, **Colors**, **Composition**: how much each kind of likeness counts. Presets: Balanced, Same vibe, Same colors, Same pose.
   - **Same kind of picture**: keeps photos with photos (no drawings or shop listings).
   - **Same framing**: close-ups with close-ups, full body with full body.
   - **Same details**: same hair color, glasses or not, dark or bright lighting as your image.
   - Near-duplicate cutoff, same-picture tolerance, minimum size, orientation, and whether copies of your own image may appear.
4. Click an image to drop it; the next best takes its place. In **All**, click any image to force it in.
5. **Save** writes the picked images as `01.jpg`, `02.jpg`, … to `<your image's folder>/<name>_similar` (editable). Saving again adds `11.jpg` and up; nothing is overwritten.

**Search** settings apply to the next search: number of images, candidates per image, Bing guesses and how long to wait for them, pages, parallel downloads, time limit, minimum file size, each AI model on/off, and Bing filters (photos only, people / head & shoulders, size, shape, color, SafeSearch). You can also add words to each guess, or add your own searches.

The AI models, DINOv2 ViT-B/14 (347 MB) and CLIP ViT-B/32 (89 MB), download once to `~/.pin-palette/models`. Each is pinned to a fixed commit and SHA-256, and runs locally on your CPU. Without them, ranking falls back to colors and composition. Searches favour accuracy over speed and take about 20–40 seconds.

## Pinterest login (optional)

Logged-in searches return more pins. In **Settings**, paste the value of the `_pinterest_sess` cookie (on pinterest.com press F12, open Application, then Cookies). It is stored only on your computer, in `~/.pin-palette/config.json`.

## How matching works

Each image is reduced to up to 8 colors in CIELAB space. Two palettes are compared on:

- **Color distance**: average shift between matched colors (earth mover's distance, in ΔE).
- **Share mismatch**: the largest difference in how much of the image a color covers.
- **Color count**: how many colors cover 5% or more.

When you add several images, their palettes are merged into one target palette, which you can edit.

### Similar image mode

Each candidate gets four similarity signals: DINOv2 embeddings (class token plus mean patch token, cosine), CLIP image embeddings (cosine), an HSV color histogram (intersection), and an 8×8 color layout (mirror-invariant). Each signal is z-scored across the candidates, so the weights mean importance rather than scale. Then they are blended with your weights. CLIP also classifies every image against fixed text prompts (`clip_prompts.json`, rebuilt with `tools/gen_clip_prompts.py`): photo, drawing or shop listing; close-up, waist-up, full body or from behind; hair color; glasses; dark or bright lighting. Images whose class differs from your image's are penalised. Detail penalties are scaled by how confident the classification of your image is. Duplicates are caught by perceptual hash (same picture) and by CLIP similarity (same shoot, re-cropped).

## Tests

```sh
python -m unittest discover -s tests
```

CI runs them on Windows, macOS and Linux with Python 3.10 and 3.13.

## Requirements

Python 3.10+, Pillow, NumPy and ONNX Runtime. Runs locally at `http://127.0.0.1:8765`.

## License

[MIT](LICENSE)
