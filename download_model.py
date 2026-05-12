#!/usr/bin/env python3
"""
One-time script: download the faster-whisper-small model into models/whisper-small/
Run once inside the backend container (or on the host if huggingface_hub is installed):

    docker exec gg_fin_backend python /app/download_model.py
"""
from pathlib import Path

MODEL_DIR = Path(__file__).parent / "models" / "whisper-small"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

print(f"Downloading Systran/faster-whisper-small → {MODEL_DIR}")

try:
    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id="Systran/faster-whisper-small",
        local_dir=str(MODEL_DIR),
        local_dir_use_symlinks=False,
    )
    print("✓ Model downloaded successfully.")
except Exception as e:
    print(f"huggingface_hub download failed: {e}")
    print("Falling back to faster_whisper built-in download …")
    from faster_whisper import WhisperModel
    import shutil, os
    m = WhisperModel("small", device="cpu", compute_type="int8")
    # faster_whisper caches to ~/.cache — copy to our target dir
    cache_root = Path.home() / ".cache" / "huggingface" / "hub"
    candidates = list(cache_root.glob("models--Systran--faster-whisper-small/snapshots/*"))
    if candidates:
        src = candidates[-1]
        for f in src.iterdir():
            shutil.copy2(f, MODEL_DIR / f.name)
        print(f"✓ Copied from cache {src} → {MODEL_DIR}")
    else:
        print("Warning: could not locate cache — model will download on first use.")
