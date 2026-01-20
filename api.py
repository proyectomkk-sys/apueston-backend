import html
import time
import os
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

BOT_TOKEN = os.getenv("BOT_TOKEN")
TICKETS_GROUP_ID = int(os.getenv("TICKETS_GROUP_ID", "-1003575621343"))

if not BOT_TOKEN:
    raise RuntimeError("Falta BOT_TOKEN")

TG_SEND = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

app = FastAPI()

# Permitir llamadas desde GitHub Pages
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://proyectomkk-sys.github.io"],
    allow_methods=["POST"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/report/cajeros")
async def report_cajeros(payload: dict):
    user = payload.get("user") or {}

    issue = payload.get("issue")
    if not issue:
        raise HTTPException(status_code=400, detail="Falta issue")

    full_name = user.get("full_name") or "Sin nombre"
    username = user.get("username") or "sin_username"
    user_id = user.get("id") or "desconocido"

    cause = payload.get("cause") or "No especificada"
    solution = payload.get("solution") or "No especificada"

    ts = int(time.time() * 1000)

    text = (
        "🎫 <b>TICKET NUEVO</b>\n"
        f"👤 <b>Usuario:</b> {html.escape(full_name)} | @{html.escape(username)}\n"
        f"🆔 <b>Chat ID:</b> <code>{user_id}</code>\n"
        f"🕒 <b>Timestamp:</b> <code>{ts}</code>\n\n"
        f"⚠️ <b>Incidente:</b> {html.escape(issue)}\n"
        f"🔎 <b>Causa:</b> {html.escape(cause)}\n"
        f"✅ <b>Solución:</b> {html.escape(solution)}"
    )

    r = requests.post(
        TG_SEND,
        json={
            "chat_id": TICKETS_GROUP_ID,
            "text": text,
            "parse_mode": "HTML"
        },
        timeout=15
    )

    if not r.ok:
        raise HTTPException(status_code=502, detail=r.text)

    return {"ok": True}