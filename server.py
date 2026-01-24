# server.py
import os
import time
import json
import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from html import escape

# 1) Token PRINCIPAL (para publicar tickets al grupo)
GROUP_BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# 2) Tokens por bot ORIGEN (para responder al usuario)
BOT_TOKENS = {
    "HS Call Center": os.environ.get("BOT_TOKENA", "").strip(),  # nuevo bot
    "Soporte Bet Cajeros 24/7": os.environ.get("BOT_TOKENB", "").strip(),  # bot antiguo
}

TICKETS_GROUP_ID = int(os.environ.get("TICKETS_GROUP_ID", "-1003575621343"))

if not GROUP_BOT_TOKEN:
    raise RuntimeError("Falta BOT_TOKEN (token principal) en variables de entorno")

TELEGRAM_API_GROUP = f"https://api.telegram.org/bot{GROUP_BOT_TOKEN}"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def tg_send_message(api_base: str, chat_id: int, text: str, parse_mode: str = "HTML"):
    r = requests.post(
        f"{api_base}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        timeout=30
    )
    return r

def tg_send_photo(api_base: str, chat_id: int, caption: str, photo_bytes: bytes, filename: str, parse_mode: str = "HTML"):
    files = {"photo": (filename, photo_bytes)}
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode}
    r = requests.post(f"{api_base}/sendPhoto", data=data, files=files, timeout=60)
    return r

@app.get("/")
def root():
    return {"ok": True, "service": "apueston-backend"}

@app.post("/ticket")
async def create_ticket(
    payload: str = Form(...),
    screenshot: UploadFile | None = File(None)
):
    # Parsear JSON
    try:
        data = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="payload inválido (JSON)")

    if data.get("type") != "reporte_falla":
        raise HTTPException(status_code=400, detail="type inválido")

    # Bot origen: esperamos "a" o "b"
    source_bot = (data.get("source_bot") or "").strip().lower() or "desconocido"

    user = data.get("user") or {}
    desc = (data.get("description") or "").strip()
    ts = int(data.get("ts") or int(time.time() * 1000))

    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id faltante")

    if len(desc) < 5:
        raise HTTPException(status_code=400, detail="Descripción muy corta")

    # 1) Responder al usuario usando EL BOT ORIGEN (si hay token)
    origin_token = BOT_TOKENS.get(source_bot, "")
    if origin_token:
        api_origin = f"https://api.telegram.org/bot{origin_token}"
        r = tg_send_message(api_origin, int(user_id), "✅ Recibimos tu reporte. Gracias por avisarnos.", "HTML")
        if not r.ok:
            # 403 -> usuario no inició chat / bloqueó el bot
            print(f"[WARN] No se pudo responder al usuario con bot '{source_bot}': {r.text}")
    else:
        print(f"[WARN] No hay token configurado para source_bot='{source_bot}'")

    # 2) Armar mensaje al grupo
    first = user.get("first_name", "")
    last = user.get("last_name", "")
    full_name = (" ".join([first, last]).strip() or "Sin nombre")
    username = (user.get("username") or "sin_username")

    # Escapar para HTML
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

    # 3) Enviar al grupo con el BOT PRINCIPAL
    if screenshot and screenshot.filename:
        photo_bytes = await screenshot.read()
        if photo_bytes:
            r = tg_send_photo(TELEGRAM_API_GROUP, TICKETS_GROUP_ID, ticket_text, photo_bytes, screenshot.filename, "HTML")
            if not r.ok:
                raise HTTPException(status_code=500, detail=r.text)
            return {"ok": True, "sent": "photo"}

    r = tg_send_message(TELEGRAM_API_GROUP, TICKETS_GROUP_ID, ticket_text, "HTML")
    if not r.ok:
        raise HTTPException(status_code=500, detail=r.text)

    return {"ok": True, "sent": "message"}
