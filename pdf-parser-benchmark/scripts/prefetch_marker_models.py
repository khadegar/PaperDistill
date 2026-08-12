#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path


FONT_URL = "https://models.datalab.to/artifacts/GoNotoCurrent-Regular.ttf"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prefetch Marker/Surya weights without running inference."
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    root = args.output.resolve()
    gguf_dir = root / "gguf"
    hf_home = root / "huggingface"
    model_cache = root / "datalab-models"
    font_dir = root / "fonts"
    for directory in (root, gguf_dir, hf_home, model_cache, font_dir):
        directory.mkdir(parents=True, exist_ok=True)

    font_path = font_dir / "GoNotoCurrent-Regular.ttf"
    if not font_path.is_file():
        partial = font_path.with_suffix(font_path.suffix + ".partial")
        with urllib.request.urlopen(FONT_URL) as response, partial.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
        os.replace(partial, font_path)

    os.environ["HF_HOME"] = str(hf_home)
    os.environ["MODEL_CACHE_DIR"] = str(model_cache)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TORCH_DEVICE"] = "cpu"
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    from huggingface_hub import hf_hub_download
    from surya.ocr_error.loader import _resolve_checkpoint
    from surya.settings import settings

    repo = settings.SURYA_GGUF_REPO
    gguf_files = [settings.SURYA_GGUF_MODEL_FILE, settings.SURYA_GGUF_MMPROJ_FILE]
    resolved: list[Path] = []
    for filename in gguf_files:
        resolved.append(
            Path(
                hf_hub_download(
                    repo_id=repo,
                    filename=filename,
                    local_dir=str(gguf_dir),
                )
            ).resolve()
        )

    ocr_error_dir = Path(_resolve_checkpoint(settings.OCR_ERROR_MODEL_CHECKPOINT)).resolve()
    inventory = {
        "schema_version": "1.0",
        "surya_gguf_repo": repo,
        "gguf": [
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in resolved
        ],
        "ocr_error_model": {
            "checkpoint": settings.OCR_ERROR_MODEL_CHECKPOINT,
            "path": ocr_error_dir.relative_to(root).as_posix(),
        },
        "font": {
            "source_url": FONT_URL,
            "path": font_path.relative_to(root).as_posix(),
            "bytes": font_path.stat().st_size,
            "sha256": sha256(font_path),
        },
    }
    (root / "model-inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(inventory, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
