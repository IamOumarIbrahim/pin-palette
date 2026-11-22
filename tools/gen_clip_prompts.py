#!/usr/bin/env python3
"""Rebuild clip_prompts.json: CLIP text embeddings for the zero-shot prompts that
Similar image mode uses to tell photos from drawings / shop listings, and close-ups
from full-body / from-behind shots. The app only needs the result, never the text
model or a tokenizer.

    python tools/gen_clip_prompts.py > new.json && mv new.json clip_prompts.json

Downloads tokenizer.json and text_model_quantized.onnx (~65 MB) from the same
Xenova/clip-vit-base-patch32 commit as the image model in similar.py.
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import similar  # noqa: E402

BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/6b7c523703ae26e39be4b03774966f8571c7f523/"

# (dimension, class, prompt). A class's score is its best-matching prompt.
PROMPTS = [
    ("style", "photo", "a photo of a real person"),
    ("style", "photo", "a selfie photo of a woman"),
    ("style", "photo", "a cosplay photo of a real person"),
    ("style", "drawing", "an anime drawing"),
    ("style", "drawing", "a cartoon illustration"),
    ("style", "drawing", "a screenshot from an anime"),
    ("style", "drawing", "a digital painting of a character"),
    ("style", "product", "a product photo of a costume on a mannequin"),
    ("style", "product", "an online shop listing with text and several photos"),
    ("style", "product", "a collage of several photos"),
    ("framing", "closeup", "a close-up selfie of a face"),
    ("framing", "closeup", "a close-up portrait photo of a face"),
    ("framing", "halfbody", "a waist-up photo of a person"),
    ("framing", "fullbody", "a full body photo of a person standing"),
    ("framing", "fullbody", "a full length photo of a person sitting"),
    ("framing", "back", "a photo of a person seen from behind"),
    # details: compared with the source image, weighted by the "Same details" slider
    ("hair", "blonde", "a photo of a person with blonde hair"),
    ("hair", "blonde", "a photo of a person with platinum blonde hair"),
    ("hair", "brown", "a photo of a person with brown hair"),
    ("hair", "black", "a photo of a person with black hair"),
    ("hair", "red", "a photo of a person with red hair"),
    ("hair", "dyed", "a photo of a person with pink or blue dyed hair"),
    # CLIP handles "without glasses" poorly, so "none" is the same phrase minus the glasses.
    ("eyewear", "glasses", "a selfie of a woman wearing glasses"),
    ("eyewear", "glasses", "a photo of a man wearing glasses"),
    ("eyewear", "none", "a selfie of a woman"),
    ("eyewear", "none", "a photo of a man"),
    ("light", "dark", "a dark dimly lit photo taken at night indoors"),
    ("light", "bright", "a bright well lit photo in daylight"),
]


def fetch(name):
    path = similar.MODEL_DIR / ("text-" + name.replace("/", "-"))
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print("downloading " + name, file=sys.stderr)
        urllib.request.urlretrieve(BASE + name, path)
    return path


class Tokenizer:
    """Minimal CLIP BPE. Prompts are plain lowercase ASCII, so the byte-level mapping is identity."""

    def __init__(self, path):
        m = json.loads(Path(path).read_text(encoding="utf-8"))["model"]
        self.vocab = m["vocab"]
        self.ranks = {tuple(x.split(" ")): i for i, x in enumerate(m["merges"])}

    def bpe(self, word):
        sym = list(word[:-1]) + [word[-1] + "</w>"]
        while len(sym) > 1:
            pairs = [(self.ranks.get((a, b), 1 << 30), i) for i, (a, b) in enumerate(zip(sym, sym[1:]))]
            r, i = min(pairs)
            if r == 1 << 30:
                break
            sym[i:i + 2] = [sym[i] + sym[i + 1]]
        return sym

    def encode(self, text):
        words = re.findall(r"'s|'t|'re|'ve|'m|'ll|'d|[a-z]+|[0-9]|[^\sa-z0-9]+", " ".join(text.lower().split()))
        return ([self.vocab["<|startoftext|>"]] + [self.vocab[t] for w in words for t in self.bpe(w)]
                + [self.vocab["<|endoftext|>"]])


def main():
    tok = Tokenizer(fetch("tokenizer.json"))
    sess = ort.InferenceSession(str(fetch("onnx/text_model_quantized.onnx")), providers=["CPUExecutionProvider"])
    names = [i.name for i in sess.get_inputs()]
    out = []
    for dim, cls, text in PROMPTS:
        ids = np.array([tok.encode(text)], np.int64)
        feed = {n: (ids if n == "input_ids" else np.ones_like(ids)) for n in names}
        e = sess.run(["text_embeds"], feed)[0][0]
        out.append({"dim": dim, "class": cls, "text": text, "emb": (e / np.linalg.norm(e)).tolist()})
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
