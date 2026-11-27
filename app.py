
import os
import re
import time
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="ClipFinder AI")

app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE, "static")),
    name="static"
)

kick_token = None
kick_token_expiry = 0


@app.get("/")
def home():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.get("/health")
def health():
    return {"ok": True, "service": "ClipFinder AI"}


async def get_kick_token():
    global kick_token, kick_token_expiry

    if kick_token and time.time() < kick_token_expiry:
        return kick_token

    client_id = os.getenv("KICK_CLIENT_ID")
    client_secret = os.getenv("KICK_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise HTTPException(
            503,
            "Faltan las credenciales de Kick en Render."
        )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://id.kick.com/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret
                }
            )

        if response.status_code != 200:
            raise HTTPException(
                502,
                detail={
                    "message": "Error de autenticación con Kick.",
                    "status": response.status_code,
                    "response": response.text[:500]
                }
            )

        result = response.json()
        kick_token = result["access_token"]
        kick_token_expiry = (
            time.time() + int(result.get("expires_in", 3600)) - 60
        )

        return kick_token

    except httpx.RequestError:
        raise HTTPException(
            502,
            "No se pudo conectar con Kick."
        )
    except (KeyError, ValueError):
        raise HTTPException(
            502,
            "Respuesta inesperada de Kick."
        )


def identify_link(url):
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise HTTPException(400, "El enlace debe utilizar HTTPS.")

    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/").split("/")

    if host == "kick.com":
        if not path or not path[0]:
            raise HTTPException(400, "No se encontró un canal.")

        slug = path[0].lower()

        if not re.fullmatch(r"[a-z0-9_-]+", slug):
            raise HTTPException(400, "Nombre de canal inválido.")

        return {"platform": "Kick", "slug": slug}

    if host in ("youtube.com", "www.youtube.com", "youtu.be"):
        return {"platform": "YouTube"}

    raise HTTPException(400, "Plataforma no compatible.")


async def get_kick_channel(slug):
    token = await get_kick_token()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.kick.com/public/v1/channels",
                params={"slug": slug},
                headers={"Authorization": f"Bearer {token}"}
            )

        if response.status_code != 200:
            raise HTTPException(
                502,
                detail={
                    "message": "Kick rechazó la consulta del canal.",
                    "status": response.status_code,
                    "response": response.text[:500]
                }
            )

        result = response.json()
        channels = result.get("data", [])

        if not channels:
            raise HTTPException(
                404,
                "No se encontró el canal de Kick."
            )

        return channels[0]

    except httpx.RequestError:
        raise HTTPException(
            502,
            "No se pudo consultar el canal en Kick."
        )


@app.get("/kick/status")
async def kick_status():
    await get_kick_token()

    return {
        "connected": True,
        "service": "Kick API",
        "message": "Autenticación con Kick completada."
    }


@app.post("/check-link")
@app.post("/analyze-link")
async def check_link(data: dict):
    url = str(data.get("url", "")).strip()

    if not url:
        raise HTTPException(400, "Introduce un enlace.")

    info = identify_link(url)

    if info["platform"] == "YouTube":
        return {
            "status": "link_recognized",
            "platform": "YouTube",
            "message": (
                "Enlace reconocido. El análisis requiere "
                "acceso autorizado al contenido audiovisual."
            )
        }

    channel = await get_kick_channel(info["slug"])

    stream = channel.get("stream") or {}
    is_live = bool(stream.get("is_live", False))

    return {
        "status": "link_recognized",
        "platform": "Kick",
        "channel": channel.get("slug", info["slug"]),
        "title": channel.get("stream_title") or channel.get("title"),
        "is_live": is_live,
        "viewer_count": stream.get("viewer_count"),
        "message": (
            "Canal encontrado. Está transmitiendo en directo."
            if is_live
            else "Canal encontrado. Actualmente no está en directo."
        ),
        "analysis_available": False,
        "analysis_note": (
            "La API proporciona información del canal, "
            "pero todavía necesitamos acceso autorizado "
            "al vídeo para analizarlo."
        )
    }
