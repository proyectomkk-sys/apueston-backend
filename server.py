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
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, filters

# ----------------------------
# ENV
# ----------------------------
TICKETS_GROUP_ID = int(os.environ.get("TICKETS_GROUP_ID", "-1003575621343"))
DB_PATH = os.environ.get("DB_PATH", "tickets.db")
ENABLE_LISTENER = os.environ.get("ENABLE_LISTENER", "1") == "1"


# ----------------------------
# BOT TOKENS
# ----------------------------
def load_bot_tokens() -> Dict[str, str]:
    raw = os.environ.get("BOT_TOKENS_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("BOT_TOKENS_JSON debe ser un objeto JSON {nombre: token}")

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

    raise RuntimeError("Falta BOT_TOKENS_JSON")


BOT_TOKENS = load_bot_tokens()


# ----------------------------
# SQLITE
# ----------------------------
def db_connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            bot_name TEXT NOT NULL,
            chat_id INTEGER,
            client_user_name TEXT,
            description TEXT,
            cause TEXT,
            solution TEXT,
            group_message_id INTEGER,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def db_insert_ticket(payload: dict, group_message_id: Optional[int]) -> int:
    conn = db_connect()
    cur = conn.cursor()

    created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    bot_name = str(payload.get("bot_name", "")).strip()
    client_user_name = str(payload.get("client_user_name", "")).strip()

    chat_id = payload.get("chat_id", None)
    try:
        chat_id = int(chat_id) if chat_id is not None else None
    except:
        chat_id = None

    description = str(payload.get("description", "")).strip()
    cause = str(payload.get("cause", "")).strip()
    solution = str(payload.get("solution", "")).strip()

    clean_payload = {
        "bot_name": bot_name,
        "client_user_name": client_user_name,
        "chat_id": chat_id,
        "description": description,
        "cause": cause,
        "solution": solution,
    }

    cur.execute(
        """
        INSERT INTO tickets (
            created_at, bot_name, chat_id, client_user_name, description, cause, solution,
            group_message_id, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            bot_name,
            chat_id,
            client_user_name,
            description,
            cause,
            solution,
            group_message_id,
            json.dumps(clean_payload, ensure_ascii=False),
        ),
    )

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
    r = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
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


# ----------------------------
# TICKET TEXT
# ----------------------------
def make_ticket_text(p: dict, ticket_id: int) -> str:
    bot_name = escape(str(p.get("bot_name", "")))
    client_user_name = escape(str(p.get("client_user_name", "—")))

    chat_id = p.get("chat_id", "")
    try:
        chat_id = int(chat_id) if chat_id not in ("", None) else ""
    except:
        chat_id = ""

    description = escape(str(p.get("description", "Sin descripción")))
    cause = escape(str(p.get("cause", "")))
    solution = escape(str(p.get("solution", "")))

    parts = []
    parts.append(f"🧾 <b>NUEVO TICKET</b>  <b>#TICKET-{ticket_id}</b>")
    parts.append(f"🤖 <b>Bot:</b> {bot_name}")

    if client_user_name:
        parts.append(f"👤 <b>Cliente:</b> {client_user_name}")

    if chat_id != "":
        parts.append(f"🆔 <b>Chat ID:</b> {chat_id}")

    parts.append("")
    parts.append(f"⚠️ <b>Descripción:</b>\n{description}")

    if cause:
        parts.append("")
        parts.append(f"🔎 <b>Causa:</b> {cause}")

    if solution:
        parts.append("")
        parts.append(f"✅ <b>Solución:</b> {solution}")

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
        "listener": ENABLE_LISTENER,
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

    # Guardar ticket (aún no sabemos message_id del grupo)
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

    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    if not msg.reply_to_message:
        await msg.reply_text("⚠️ Responde (reply) al ticket y escribe: /r tu mensaje")
        return

    replied_text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    if not replied_text:
        await msg.reply_text("⚠️ El mensaje respondido no tiene texto/caption para leer el #TICKET.")
        return

    m = TICKET_RE.search(replied_text)
    if not m:
        await msg.reply_text("⚠️ No encuentro el #TICKET-123 en el mensaje respondido.")
        return

    ticket_id = int(m.group(1))
    ticket = db_get_ticket(ticket_id)
    if not ticket:
        await msg.reply_text(f"⚠️ No existe #TICKET-{ticket_id} en la base.")
        return

    chat_id = ticket["chat_id"]
    bot_name = ticket["bot_name"]

    if not chat_id:
        await msg.reply_text("⚠️ Este ticket no tiene chat_id guardado (no puedo responder al cliente).")
        return

    raw = msg.text or ""
    answer = raw.split(" ", 1)[1].strip() if " " in raw else ""
    if not answer:
        await msg.reply_text("⚠️ Usa: /r tu mensaje")
        return

    token = pick_token(bot_name)
    final_text = (
        f"📩 <b>Respuesta de Soporte</b>\n"
        f"<b>#TICKET-{ticket_id}</b>\n\n"
        f"{escape(answer)}"
    )

    try:
        tg_send_message(token, int(chat_id), final_text)
        await msg.reply_text(f"✅ Respuesta enviada al cliente por <b>{escape(bot_name)}</b>.", parse_mode="HTML")
    except Exception as e:
        await msg.reply_text(f"❌ No pude enviar al cliente: {e}")


# ----------------------------
# START LISTENERS (1 thread por bot)
# ----------------------------
def start_listener_threads_once():
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
