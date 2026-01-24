# server.py
import os
import time
import json
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from html import escape

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TICKETS_GROUP_ID = int(os.environ.get("TICKETS_GROUP_ID", "-1003575621343"))

if not BOT_TOKEN:
    raise RuntimeError("Falta BOT_TOKEN en variables de entorno")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI()

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

def send_photo(chat_id: int, caption: str, photo_bytes: bytes, filename: str = "screenshot.jpg", parse_mode: str = "HTML"):
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
    # 1) parsear JSON del campo payload
    try:
        data = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="payload inválido (JSON)")

    if data.get("type") != "reporte_falla":
        raise HTTPException(status_code=400, detail="type inválido")

    # 2) leer bot origen (si llega)
    source_bot = (data.get("source_bot") or "").strip() or "desconocido"

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

    # 3) escapar para HTML (evita que se rompa el parse_mode)
    full_name_e = escape(full_name)
    username_e = escape(username)
    desc_e = escape(desc)
    source_bot_e = escape(source_bot)

    ticket_text = (
        "🎫 <b>TICKET NUEVO</b>\n"
        f"🤖 <b>Bot origen:</b> {source_bot_e}\n"
        f"👤 <b>Usuario:</b> {full_name_e} | @{username_e} | id:{user_id}\n"
        f"🕒 <b>Timestamp:</b> <code>{ts}</code>\n\n"
        f"📝 <b>Descripción:</b>\n{desc_e}"
    )

    # 4) enviar (foto si hay screenshot)
    if screenshot and screenshot.filename:
        photo_bytes = await screenshot.read()
        if not photo_bytes:
            res = send_message(TICKETS_GROUP_ID, ticket_text, parse_mode="HTML")
            return {"ok": True, "sent": "message", "telegram": res}

        res = send_photo(
            TICKETS_GROUP_ID,
            ticket_text,
            photo_bytes,
            filename=screenshot.filename,
            parse_mode="HTML"
        )
        return {"ok": True, "sent": "photo", "telegram": res}

    res = send_message(TICKETS_GROUP_ID, ticket_text, parse_mode="HTML")
    return {"ok": True, "sent": "message", "telegram": res}
