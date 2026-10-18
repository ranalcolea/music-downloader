#!/usr/bin/env python3
# SHAZAMIO_SERVIDOR_PRINCIPAL_V1

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from shazamio import Shazam


async def recognize(path):
    shazam = Shazam()
    try:
        data = await asyncio.wait_for(
            shazam.recognize(path),
            timeout=19
        )
        track = data.get("track") or {}

        return {
            "status": "success",
            "result": (
                {
                    "artist": str(track.get("subtitle") or "").strip(),
                    "title": str(track.get("title") or "").strip()
                }
                if track else None
            )
        }
    finally:
        close = getattr(shazam, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result


def main():
    if len(sys.argv) != 2:
        raise ValueError("Falta el archivo de audio.")

    source = Path(sys.argv[1])

    if not source.is_file():
        raise ValueError("Archivo no encontrado.")

    with tempfile.TemporaryDirectory(
        prefix="music-shazam-"
    ) as folder:

        wav = Path(folder) / "audio.wav"

        command = [
            "/usr/bin/ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(source),
            "-t", "12",
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            str(wav)
        ]

        subprocess.run(
            command,
            capture_output=True,
            check=True,
            timeout=7
        )

        if not wav.is_file() or wav.stat().st_size < 100000:
            raise ValueError("Audio insuficiente.")

        result = asyncio.run(recognize(str(wav)))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
