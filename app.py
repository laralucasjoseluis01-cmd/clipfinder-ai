
import math
import shutil
import struct
import subprocess
import uuid
import wave
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent
TEMP = BASE / "tmp"
TEMP.mkdir(exist_ok=True)

app = FastAPI(title="ClipFinder AI")

app.mount(
    "/static",
    StaticFiles(directory=BASE / "static"),
    name="static"
)

@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/health")
def health():
    return {"ok": True, "service": "ClipFinder AI"}

@app.post("/analyze-upload")
async def analyze_upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()

    allowed = {
        ".mp4", ".mov", ".mkv", ".webm",
        ".mp3", ".wav", ".m4a"
    }

    if ext not in allowed:
        raise HTTPException(400, "Formato no compatible")

    token = uuid.uuid4().hex
    source = TEMP / f"{token}{ext}"
    audio = TEMP / f"{token}.wav"

    try:
        with source.open("wb") as output:
            shutil.copyfileobj(file.file, output)

        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(source),
                "-vn", "-ac", "1", "-ar", "16000",
                str(audio)
            ],
            capture_output=True,
            text=True,
            timeout=900
        )

        if result.returncode != 0:
            raise HTTPException(422, "No se pudo procesar el audio")

        with wave.open(str(audio), "rb") as wav:
            rate = wav.getframerate()
            frames = wav.getnframes()
            raw = wav.readframes(frames)

        samples = struct.unpack(
            "<" + "h" * (len(raw) // 2), raw
        )

        window = rate // 2
        hop = rate // 4
        moments = []

        for start in range(0, len(samples) - window, hop):
            chunk = samples[start:start + window]

            if not chunk:
                continue

            intensity = math.sqrt(
                sum(x * x for x in chunk) / len(chunk)
            ) / 32768

            moments.append({
                "seconds": round(start / rate, 2),
                "intensity": round(intensity, 4)
            })

        selected = []

        for moment in sorted(
            moments,
            key=lambda x: x["intensity"],
            reverse=True
        ):
            if all(
                abs(moment["seconds"] - item["seconds"]) > 12
                for item in selected
            ):
                selected.append(moment)

            if len(selected) >= 10:
                break

        selected.sort(key=lambda x: x["seconds"])

        return {
            "duration_seconds": round(frames / rate, 2),
            "moments": selected,
            "note": "Momentos candidatos por intensidad de audio. Revisa cada uno."
        }

    finally:
        source.unlink(missing_ok=True)
        audio.unlink(missing_ok=True)

@app.post("/analyze-link")
def analyze_link(data: dict):
    url = str(data.get("url", "")).strip()

    if not url.startswith(("https://", "http://")):
        raise HTTPException(400, "Enlace inválido")

    return {
        "status": "source_required",
        "message": (
            "Para analizar este enlace necesitamos acceso oficial "
            "autorizado al video. También puedes subir el archivo."
        )
    }
