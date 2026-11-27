
import os
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
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )

        if response.status_code != 200:
            raise HTTPException(
                502,
                "Kick no aceptó las credenciales. Revisa la configuración."
            )

        result = response.json()
        kick_token = result["access_token"]
        kick_token_expiry = (
            time.time() + int(result.get("expires_in", 3600)) - 60
        )

        return kick_token

    except httpx.RequestError:
        raise HTTPException(502, "No se pudo conectar con Kick.")
    except (KeyError, ValueError):
        raise HTTPException(502, "Respuesta inesperada de Kick.")


@app.get("/kick/status")
async def kick_status():
    token = await get_kick_token()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.kick.com/public/v1/categories",
                headers={"Authorization": f"Bearer {token}"}
            )

        if response.status_code != 200:
            raise HTTPException(
                502,
                "Se obtuvo el token, pero la API no respondió correctamente."
            )

        return {
            "connected": True,
            "service": "Kick API",
            "message": "Conexión con Kick comprobada correctamente."
        }

    except httpx.RequestError:
        raise HTTPException(502, "No se pudo consultar la API de Kick.")


def identify_platform(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise HTTPException(400, "Utiliza un enlace HTTPS.")

    if host == "kick.com" or host.endswith(".kick.com"):
        return "Kick"

    if (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtu.be"
    ):
        return "YouTube"

    raise HTTPException(400, "Plataforma no compatible.")


@app.post("/check-link")
@app.post("/analyze-link")
async def check_link(data: dict):
    url = str(data.get("url", "")).strip()

    if not url:
        raise HTTPException(400, "Introduce un enlace.")

    platform = identify_platform(url)

    if platform == "Kick":
        await get_kick_token()

        return {
            "status": "link_recognized",
            "platform": "Kick",
            "api_connected": True,
            "message": (
                "Enlace reconocido y conexión con Kick comprobada. "
                "Todavía necesitamos acceso autorizado al vídeo "
                "para analizar sus momentos."
            )
        }

    return {
        "status": "link_recognized",
        "platform": "YouTube",
        "api_connected": False,
        "message": (
            "Enlace de YouTube reconocido. "
            "El análisis requiere acceso autorizado al contenido."
        )
    }
