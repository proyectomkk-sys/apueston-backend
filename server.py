import os
import re
import json
import time
import sqlite3
import threading
import requests
from html import escape
from typing import Optional, Dict

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    filters,
)

# ----------------------------
# ENV
# ----------------------------
TICKETS_GROUP_ID = int(os.environ.get("TICKETS_GROUP_ID", "-1003575621343"))
DB_PATH = os.environ.get("DB_PATH", "tickets.db")
ENABLE_LISTENER = os.environ.get("ENABLE_LISTENER", "1") == "1"

# Map: bot_name -> token
def load_bot_tokens() -> Dict[str, str]:
    raw = os.environ.get("BOT_TOKENS_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("BOT_TOKENS_JSON debe ser un objeto JSON {nombre: token}")
            # limpieza
            out = {}
            for k, v in data.items():
                k = str(k).strip()
                v = str(v).strip()
                if k and v:
                    out[k] = v
            if not out:
                raise ValueError("BOT_TOKENS_JSON está vacío")
            return out
        except Exception as e:
            raise RuntimeError(f"BOT_TOKENS_JSON inválido: {e}")

    # Fallback por si aún usas 2 bots como antes
    a = os.environ.get("BOT_TOKEN_BOTA", "").strip()
    b = os.environ.get("BOT_TOKEN_BOTB", "").strip()
    if a and b:
        return {
            "HS Call Center": a,
            "Soporte Bet Cajeros 24/7": b,
        }

    raise RuntimeError("Faltan BOT_TOKENS_JSON o BOT_TOKEN_BOTA/BOT_TOKEN_BOTB")


BOT_TOKENS = load_bot_tokens()

# ----------------------------
# SQLITE
# ----------------------------
def db_connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        bot_name TEXT NOT NULL,
        reporter_chat_id INTEGER,
        reporter_name TEXT,
        usuario TEXT,
        sucursal TEXT,
        categoria TEXT,
        prioridad TEXT,
        descripcion TEXT,
        telefono TEXT,
        equipo TEXT,
        group_message_id INTEGER,
        payload_json TEXT NOT NULL
    )
    """)
    conn.commit()
    conn.close()

def db_insert_ticket(payload: dict, group_message_id: Optional[int]) -> int:
    conn = db_connect()
    cur = conn.cursor()

    created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    bot_name = str(payload.get("bot_name", "")).strip()

    reporter_chat_id = payload.get("reporter_chat_id", None)
    try:
        reporter_chat_id = int(reporter_chat_id) if reporter_chat_id is not None else None
    except:
        reporter_chat_id = None

    reporter_name = str(payload.get("reporter_name", "") or "").strip()

    usuario = str(payload.get("usuario", "") or "").strip()
    sucursal = str(payload.get("sucursal", "") or "").strip()
    categoria = str(payload.get("categoria", "") or "").strip()
    prioridad = str(payload.get("prioridad", "") or "").strip()
    descripcion = str(payload.get("descripcion", "") or "").strip()
    telefono = str(payload.get("telefono", "") or "").strip()
    equipo = str(payload.get("equipo", "") or "").strip()

    cur.execute("""
        INSERT INTO tickets (
            created_at, bot_name, reporter_chat_id, reporter_name,
            usuario, sucursal, categoria, prioridad, descripcion, telefono, equipo,
            group_message_id, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        created_at, bot_name, reporter_chat_id, reporter_name,
        usuario, sucursal, categoria, prioridad, descripcion, telefono, equipo,
        group_message_id, json.dumps(payload, ensure_ascii=False)
    ))

    conn.commit()
    ticket_id = int(cur.lastrowid)
    conn.close()
    return ticket_id

def db_update_group_message_id(ticket_id: int, group_message_id: int):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE tickets SET group_message_id=? WHERE id=?", (group_message_id, ticket_id))
    conn.commit()
    conn.close()

def db_get_ticket(ticket_id: int):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,))
    row = cur.fetchone()
    conn.close()
    return row

# ----------------------------
# TELEGRAM API (requests)
# ----------------------------
def telegram_api(token: str) -> str:
    return f"https://api.telegram.org/bot{token}"

def tg_send_message(token: str, chat_id: int, text: str, parse_mode: str = "HTML"):
    url = f"{telegram_api(token)}/sendMessage"
    r = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }, timeout=30)
    if not r.ok:
        raise HTTPException(status_code=502, detail=f"Telegram sendMessage falló: {r.text}")
    return r.json()

def tg_send_photo(token: str, chat_id: int, caption: str, file_bytes: bytes, filename: str = "screenshot.jpg"):
    url = f"{telegram_api(token)}/sendPhoto"
    files = {"photo": (filename, file_bytes)}
    data = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}
    r = requests.post(url, data=data, files=files, timeout=60)
    if not r.ok:
        raise HTTPException(status_code=502, detail=f"Telegram sendPhoto falló: {r.text}")
    return r.json()

def pick_token(bot_name: str) -> str:
    token = BOT_TOKENS.get(bot_name, "").strip()
    if not token:
        raise HTTPException(status_code=400, detail=f"bot_name inválido. Usa: {list(BOT_TOKENS.keys())}")
    return token

def make_ticket_text(p: dict, ticket_id: int) -> str:
    bot_name = escape(str(p.get("bot_name", "")))
    categoria = escape(str(p.get("categoria", "Sin categoría")))
    usuario = escape(str(p.get("usuario", "Sin usuario")))
    sucursal = escape(str(p.get("sucursal", "Sin sucursal")))
    descripcion = escape(str(p.get("descripcion", "Sin descripción")))
    telefono = escape(str(p.get("telefono", "")))
    equipo = escape(str(p.get("equipo", "")))
    prioridad = escape(str(p.get("prioridad", "Normal")))
    reporter_name = escape(str(p.get("reporter_name", "")))

    parts = []
    parts.append(f"🧾 <b>NUEVO TICKET</b>  <b>#TICKET-{ticket_id}</b>")
    parts.append(f"🤖 <b>Bot:</b> {bot_name}")
    if reporter_name:
        parts.append(f"👥 <b>Cliente:</b> {reporter_name}")
    parts.append(f"🏷️ <b>Categoría:</b> {categoria}")
    parts.append(f"⚡ <b>Prioridad:</b> {prioridad}")
    parts.append(f"👤 <b>Usuario:</b> {usuario}")
    parts.append(f"🏢 <b>Sucursal:</b> {sucursal}")
    if telefono:
        parts.append(f"📞 <b>Tel:</b> {telefono}")
    if equipo:
        parts.append(f"💻 <b>Equipo:</b> {equipo}")
    parts.append("")
    parts.append(f"📝 <b>Descripción:</b>\n{descripcion}")
    parts.append("")
    parts.append("↩️ <i>Para responder: responde a este mensaje con</i> <b>/r tu mensaje</b>")
    return "\n".join(parts)

# ----------------------------
# FASTAPI
# ----------------------------
app = FastAPI()
db_init()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # luego restringimos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {
        "ok": True,
        "service": "telegram-multibot-backend+listener",
        "bots": list(BOT_TOKENS.keys()),
        "db_path": DB_PATH,
        "listener": ENABLE_LISTENER
    }

@app.post("/ticket")
async def create_ticket(
    payload: str = Form(...),
    screenshot: Optional[UploadFile] = File(None),
):
    try:
        p = json.loads(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"payload no es JSON válido: {e}")

    bot_name = str(p.get("bot_name", "")).strip()
    if not bot_name:
        raise HTTPException(status_code=400, detail="Falta payload.bot_name")

    token = pick_token(bot_name)

    # Guardamos primero (group_message_id aún no lo sabemos)
    ticket_id = db_insert_ticket(p, group_message_id=None)

    text = make_ticket_text(p, ticket_id)

    # Enviar al grupo
    if screenshot is not None:
        file_bytes = await screenshot.read()
        caption = text[:900] + "…" if len(text) > 900 else text
        res = tg_send_photo(token, TICKETS_GROUP_ID, caption, file_bytes, filename=screenshot.filename or "screenshot.jpg")
        group_message_id = res.get("result", {}).get("message_id")
    else:
        res = tg_send_message(token, TICKETS_GROUP_ID, text)
        group_message_id = res.get("result", {}).get("message_id")

    if group_message_id:
        db_update_group_message_id(ticket_id, int(group_message_id))

    return {"ok": True, "ticket_id": ticket_id, "bot_used": bot_name, "group_message_id": group_message_id}


# ----------------------------
# LISTENER: /r (reply) en el grupo
# ----------------------------
TICKET_RE = re.compile(r"#TICKET-(\d+)", re.IGNORECASE)

async def r_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat

    # Solo grupos
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    # Debe ser reply
    if not msg.reply_to_message or not msg.reply_to_message.text:
        await msg.reply_text("⚠️ Responde (reply) al ticket y escribe: /r tu mensaje")
        return

    # Extraer ticket_id del mensaje respondido
    m = TICKET_RE.search(msg.reply_to_message.text)
    if not m:
        await msg.reply_text("⚠️ No encuentro el #TICKET-123 en el mensaje respondido.")
        return

    ticket_id = int(m.group(1))
    ticket = db_get_ticket(ticket_id)
    if not ticket:
        await msg.reply_text(f"⚠️ No existe #TICKET-{ticket_id} en la base.")
        return

    reporter_chat_id = ticket["reporter_chat_id"]
    bot_name = ticket["bot_name"]

    if not reporter_chat_id:
        await msg.reply_text("⚠️ Este ticket no tiene reporter_chat_id guardado (no puedo responder al cliente).")
        return

    # Texto de respuesta (lo que viene después de /r)
    # Telegram manda "/r ..." como texto completo
    raw = msg.text or ""
    answer = raw.split(" ", 1)[1].strip() if " " in raw else ""
    if not answer:
        await msg.reply_text("⚠️ Escribe tu respuesta: /r tu mensaje")
        return

    # Enviar al cliente usando el BOT correcto del ticket
    token = pick_token(bot_name)
    final_text = (
        f"📩 <b>Respuesta de Soporte</b>\n"
        f"<b>#TICKET-{ticket_id}</b>\n\n"
        f"{escape(answer)}"
    )

    # Mandar al usuario
    try:
        tg_send_message(token, int(reporter_chat_id), final_text)
        await msg.reply_text(f"✅ Enviado al cliente por <b>{escape(bot_name)}</b>.", parse_mode="HTML")
    except Exception as e:
        await msg.reply_text(f"❌ No pude enviar al cliente: {e}")

def start_listener_threads_once():
    # Importante: para evitar duplicados, en Render usa 1 solo proceso (sin gunicorn multiworkers).
    def run_bot_polling(token: str, name: str):
        application = ApplicationBuilder().token(token).build()
        application.add_handler(CommandHandler("r", r_command, filters.ChatType.GROUPS))

        print(f"✅ Listener activo para bot: {name}")
        application.run_polling(allowed_updates=["message"])

    for name, token in BOT_TOKENS.items():
        t = threading.Thread(target=run_bot_polling, args=(token, name), daemon=True)
        t.start()

@app.on_event("startup")
def on_startup():
    if ENABLE_LISTENER:
        start_listener_threads_once()
