
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

# Caché de autenticación de Kick
kick_token = None
kick_token_expiry = 0


@app.get("/")
def home():
    return FileResponse(
        os.path.join(BASE, "static", "index.html")
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "ClipFinder AI"
    }


# =====================================
# AUTENTICACIÓN DE KICK
# =====================================

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
                "Error de autenticación con Kick."
            )

        result = response.json()

        kick_token = result["access_token"]

        kick_token_expiry = (
            time.time()
            + int(result.get("expires_in", 3600))
            - 60
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


# =====================================
# IDENTIFICAR ENLACES
# =====================================

def identify_link(url):
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise HTTPException(
            400,
            "El enlace debe utilizar HTTPS."
        )

    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/").split("/")

    if host == "kick.com" or host == "www.kick.com":

        if not path or not path[0]:
            raise HTTPException(
                400,
                "No se encontró un canal."
            )

        slug = path[0].lower()

        if not re.fullmatch(r"[a-z0-9_-]+", slug):
            raise HTTPException(
                400,
                "Nombre de canal inválido."
            )

        return {
            "platform": "Kick",
            "slug": slug
        }

    if host in (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be"
    ):
        return {
            "platform": "YouTube"
        }

    raise HTTPException(
        400,
        "Plataforma no compatible."
    )


# =====================================
# CONSULTAR CANALES DE KICK
# =====================================

async def get_kick_channel(slug):

    token = await get_kick_token()

    try:
        async with httpx.AsyncClient(timeout=20) as client:

            response = await client.get(
                "https://api.kick.com/public/v1/channels",
                params={"slug": slug},
                headers={
                    "Authorization": f"Bearer {token}"
                }
            )

        if response.status_code != 200:
            raise HTTPException(
                502,
                "Kick rechazó la consulta del canal."
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


# =====================================
# ESTADO DE KICK
# =====================================

@app.get("/kick/status")
async def kick_status():

    await get_kick_token()

    return {
        "connected": True,
        "service": "Kick API",
        "message": "Autenticación con Kick completada."
    }


# =====================================
# ESTADO DE OPENAI
# =====================================

@app.get("/openai/status")
async def openai_status():

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise HTTPException(
            503,
            "No se encontró OPENAI_API_KEY en Render."
        )

    try:
        async with httpx.AsyncClient(timeout=20) as client:

            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}"
                }
            )

        if response.status_code == 200:
            return {
                "connected": True,
                "service": "OpenAI API",
                "message": "Conexión con OpenAI completada."
            }

        if response.status_code == 401:
            raise HTTPException(
                401,
                "La clave API de OpenAI no es válida."
            )

        if response.status_code == 429:
            raise HTTPException(
                429,
                "OpenAI rechazó la solicitud por límites o facturación."
            )

        raise HTTPException(
            502,
            "No se pudo verificar la conexión con OpenAI."
        )

    except httpx.RequestError:
        raise HTTPException(
            502,
            "No se pudo conectar con OpenAI."
        )


# =====================================
# COMPROBAR ENLACES
# =====================================

@app.post("/check-link")
@app.post("/analyze-link")
async def check_link(data: dict):

    url = str(data.get("url", "")).strip()

    if not url:
        raise HTTPException(
            400,
            "Introduce un enlace."
        )

    info = identify_link(url)

    # YouTube
    if info["platform"] == "YouTube":
        return {
            "status": "link_recognized",
            "platform": "YouTube",
            "message": (
                "Enlace de YouTube reconocido. "
                "El análisis requiere acceso autorizado "
                "al contenido audiovisual."
            ),
            "analysis_available": False
        }

    # Kick
    channel = await get_kick_channel(
        info["slug"]
    )

    stream = channel.get("stream") or {}

    is_live = bool(
        stream.get("is_live", False)
    )

    return {
        "status": "link_recognized",
        "platform": "Kick",
        "channel": channel.get(
            "slug",
            info["slug"]
        ),
        "title": (
            channel.get("stream_title")
            or channel.get("title")
        ),
        "is_live": is_live,
        "viewer_count": stream.get("viewer_count"),
        "message": (
            "Canal encontrado. Está transmitiendo en directo."
            if is_live
            else "Canal encontrado. Actualmente no está en directo."
        ),
        "analysis_available": False,
        "analysis_note": (
            "El canal fue identificado correctamente. "
            "El análisis audiovisual requiere una fuente "
            "de audio y vídeo autorizada."
        )
    }
