# server.py
# FastAPI backend para recibir reportes desde la MiniApp y publicarlos en un grupo de Telegram.
# Endpoint: POST /ticket (multipart/form-data)
#   - payload: JSON string
#   - screenshot: archivo opcional

import os
import time
import json
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TICKETS_GROUP_ID = int(os.environ.get("TICKETS_GROUP_ID", "-1003575621343"))

if not BOT_TOKEN:
    raise RuntimeError("Falta BOT_TOKEN en variables de entorno")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI()

# ✅ Para que GitHub Pages pueda llamar al backend
# (Luego lo restringimos a tu dominio exacto)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def send_message(chat_id: int, text: str, parse_mode: str = "HTML"):
    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        timeout=30
    )
    if not r.ok:
        raise HTTPException(status_code=500, detail=r.text)
    return r.json()

def send_photo(chat_id: int, caption: str, photo_bytes: bytes, filename: str = "screenshot.jpg", parse_mode: str = "Markdown"):
    files = {"photo": (filename, photo_bytes)}
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode}
    r = requests.post(f"{TELEGRAM_API}/sendPhoto", data=data, files=files, timeout=60)
    if not r.ok:
        raise HTTPException(status_code=500, detail=r.text)
    return r.json()

@app.get("/")
def root():
    return {"ok": True, "service": "apueston-backend"}

@app.post("/ticket")
async def create_ticket(
    payload: str = Form(...),
    screenshot: UploadFile | None = File(None)
):
    try:
        data = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="payload inválido (JSON)")

    if data.get("type") != "reporte_falla":
        raise HTTPException(status_code=400, detail="type inválido")

    user = data.get("user") or {}
    desc = (data.get("description") or "").strip()
    ts = int(data.get("ts") or int(time.time() * 1000))

    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id faltante")

    first = user.get("first_name", "")
    last = user.get("last_name", "")
    full_name = " ".join([first, last]).strip() or "Sin nombre"
    username = user.get("username") or "sin_username"

    if len(desc) < 5:
        raise HTTPException(status_code=400, detail="Descripción muy corta")

    ticket_text = (
        "🎫 **TICKET NUEVO**\n"
        f"👤 **Usuario:** {full_name} | @{username} | id:{user_id}\n"
        f"🕒 **Timestamp:** `{ts}`\n\n"
        f"📝 **Descripción:**\n{desc}"
    )

    # Si hay captura => enviar como foto con caption
    if screenshot and screenshot.filename:
        photo_bytes = await screenshot.read()
        if not photo_bytes:
            # si vino vacío, enviamos solo texto
            res = send_message(TICKETS_GROUP_ID, ticket_text)
            return {"ok": True, "sent": "message", "telegram": res}

        res = send_photo(
            TICKETS_GROUP_ID,
            ticket_text,
            photo_bytes,
            filename=screenshot.filename
        )
        return {"ok": True, "sent": "photo", "telegram": res}

    # Sin captura => solo texto
    res = send_message(TICKETS_GROUP_ID, ticket_text)
    return {"ok": True, "sent": "message", "telegram": res}

