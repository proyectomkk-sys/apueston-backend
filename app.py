import re
import os
import requests
from fastapi import FastAPI, Request, HTTPException

# =========================================================
# CONFIG
# =========================================================
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "-1003575621343"))  # tu grupo soporte
REPLY_DISPATCHER_BOT_KEY = os.getenv("REPLY_DISPATCHER_BOT_KEY", "bot_a")

# Define tus bots aquí (keys = nombres en la URL)
# Debes setear en variables de entorno:
#   BOT_TOKEN_A, BOT_TOKEN_B, ...
BOTS = {
    "bot_a": {
        "token_env": "BOT_TOKEN_A",
        "display": "Ayuda Cajero-Referidor",
        "default_error_code": "300",
        "default_error_text": "Error 300, Metabet requiere biométrico",
    },
    "bot_b": {
        "token_env": "BOT_TOKEN_B",
        "display": "Bot Pruebas",
        "default_error_code": "601",
        "default_error_text": "Error 601, la página necesita biométrico",
    },
    # Agrega más aquí:
    # "bot_c": {"token_env":"BOT_TOKEN_C","display":"Otro Bot", ...},
}

# =========================================================
# APP
# =========================================================
app = FastAPI()

from collections import deque

PROCESSED = set()
PROCESSED_Q = deque(maxlen=5000)

def seen(update_id: int) -> bool:
    if update_id in PROCESSED:
        return True
    PROCESSED.add(update_id)
    PROCESSED_Q.append(update_id)
    # limpieza cuando el deque expulsa
    if len(PROCESSED) > 6000:
        while len(PROCESSED) > 5000:
            PROCESSED.discard(PROCESSED_Q.popleft())
    return False


CHATID_RE = re.compile(r"ChatID:\s*(-?\d+)")
TICKET_TAG = "🧾 TICKET"

# =========================================================
# Helpers Telegram
# =========================================================
def get_bot_token(bot_key: str) -> str:
    bot = BOTS.get(bot_key)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot no registrado (bot_key inválido).")

    token = os.getenv(bot["token_env"], "").strip()
    if not token:
        raise RuntimeError(f"Falta variable de entorno {bot['token_env']} para {bot_key}")
    return token

def tg(bot_key: str, method: str, payload: dict):
    token = get_bot_token(bot_key)
    api = f"https://api.telegram.org/bot{token}"
    r = requests.post(f"{api}/{method}", json=payload, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error calling {method}: {data}")
    return data["result"]

def send_message(bot_key: str, chat_id: int, text: str, reply_to_message_id: int | None = None, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg(bot_key, "sendMessage", payload)

def answer_callback_query(bot_key: str, callback_query_id: str, text: str = "", show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}
    return tg(bot_key, "answerCallbackQuery", payload)

def parse_ticket_botkey(text: str) -> str | None:
    # Busca una línea como: "BotKey: bot_a"
    m = re.search(r"BotKey:\s*([a-zA-Z0-9_]+)", text)
    return m.group(1) if m else None

def edit_reply_markup(bot_key: str, chat_id: int, message_id: int, reply_markup: dict | None):
    payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
    return tg(bot_key, "editMessageReplyMarkup", payload)

# =========================================================
# Health
# =========================================================
@app.get("/")
def health():
    return {"ok": True, "service": "telegram-support-multibot", "bots": list(BOTS.keys())}

# =========================================================
# Webhook por bot:
#   /telegram/bot_a
#   /telegram/bot_b
# =========================================================
@app.post("/telegram/{bot_key}")
async def telegram_webhook(bot_key: str, req: Request):
    if bot_key not in BOTS:
        raise HTTPException(status_code=404, detail="Bot no registrado (bot_key inválido).")

    update = await req.json()
    update_id = update.get("update_id")
    if isinstance(update_id, int) and seen(update_id):
        return {"ok": True}
    bot_display = BOTS[bot_key]["display"]

    # ----------------------------
    # 1) CallbackQuery: botón REPORTAR
    # ----------------------------
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        callback_id = cq.get("id")

        try:
            # Cerrar el spinner SIEMPRE primero
            answer_callback_query(bot_key, callback_id, "Procesando…", show_alert=False)

            if data.startswith("report:"):
                error_code = data.split(":", 1)[1].strip()

                from_user = cq.get("from", {})
                msg = cq.get("message", {})
                chat = msg.get("chat", {})

                # ⚠️ Para responder al cliente, lo más seguro es usar el chat del mensaje
                client_chat_id = chat.get("id")
                # y como backup, el id del usuario que presionó
                from_id = from_user.get("id")

                full_name = (from_user.get("first_name", "") + " " + from_user.get("last_name", "")).strip()
                username = from_user.get("username")
                uname = f"@{username}" if username else "(sin username)"

                ticket_text = (
                    f"{TICKET_TAG}\n"
                    f"🤖 Bot: {bot_display}\n"
                    f"BotKey: {bot_key}\n"
                    f"👤 Cliente: {full_name} {uname}\n"
                    f"ChatID: {client_chat_id}\n"
                    f"⚠️ Error: Error {error_code}\n"
                    f"📝 Detalle: -\n"
                    f"🧩 Causa: -\n"
                    f"✅ Solución: -\n\n"
                    f"↩️ Responde a ESTE mensaje con:\n"
                    f"/r tu respuesta aquí"
                )

                # 1) Enviar ticket al grupo
                send_message(bot_key, SUPPORT_GROUP_ID, ticket_text)

                # 2) Quitar botón (si falla, no importa)
                try:
                    msg_obj = cq.get("message", {})
                    edit_reply_markup(
                        bot_key,
                        client_chat_id,
                        msg_obj.get("message_id"),
                        {"inline_keyboard": []}
                    )
                except Exception:
                    pass

                # 3) Confirmación al cliente (si client_chat_id viene None, usa from_id)
                target_chat = client_chat_id if isinstance(client_chat_id, int) else from_id
                if isinstance(target_chat, int):
                    send_message(bot_key, target_chat, "✅ Tu reporte fue enviado. En breve soporte se comunicará contigo.")

                # 4) Cerrar callback con OK final
                answer_callback_query(bot_key, callback_id, "Reporte enviado ✅", show_alert=False)

        except Exception:
            # No dejes que el webhook devuelva 500 (evita reintentos)
            try:
                answer_callback_query(bot_key, callback_id, "Listo ✅", show_alert=False)
            except Exception:
                pass

        return {"ok": True}


    # ----------------------------
    # 2) Mensajes (comandos /start /prueba /getchatid y /r en grupo)
    # ----------------------------
    if "message" in update:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        text = (msg.get("text", "") or "").strip()

        # /start
        if text.startswith("/start"):
            send_message(bot_key, chat_id, f"Hola 👋\nUsa /prueba para ver el error con el botón REPORTAR.\nBot: {bot_display}")
            return {"ok": True}

        # /prueba (cliente)
        if text.startswith("/prueba"):
            err_text = BOTS[bot_key]["default_error_text"]
            err_code = BOTS[bot_key]["default_error_code"]
            kb = {"inline_keyboard": [[{"text": "📩 REPORTAR", "callback_data": f"report:{err_code}"}]]}
            send_message(bot_key, chat_id, err_text, reply_markup=kb)
            return {"ok": True}

        # /getchatid
        if text.startswith("/getchatid"):
            send_message(bot_key, chat_id, f"chat_id de este chat/grupo: {chat_id}")
            return {"ok": True}

        # /r (solo en el grupo soporte y como reply a ticket)
        # Nota: Telegram puede mandar "/r@TuBot" también
        # /r (solo en el grupo soporte y como reply a ticket)
        if chat_id == SUPPORT_GROUP_ID and (text.startswith("/r") or text.startswith("/r@")):
            # Solo el dispatcher procesa /r en el grupo (evita duplicados y evita depender de que bot_b reciba el update)
            if bot_key != REPLY_DISPATCHER_BOT_KEY:
                return {"ok": True}

            reply_to = msg.get("reply_to_message")
            if not reply_to or not (reply_to.get("text") or ""):
                send_message(bot_key, chat_id, "⚠️ Debes responder (reply) al mensaje del ticket y escribir: /r tu respuesta")
            return {"ok": True}

        replied_text = (reply_to.get("text") or "")
        if TICKET_TAG not in replied_text:
            send_message(bot_key, chat_id, "⚠️ El mensaje respondido no parece un ticket.")
            return {"ok": True}

        # Detectar a qué bot pertenece ese ticket (OBLIGATORIO)
        ticket_bot_key = parse_ticket_botkey(replied_text)
        if not ticket_bot_key:
            send_message(bot_key, chat_id, "⚠️ Este ticket no tiene BotKey. No puedo saber qué bot debe responder.")
            return {"ok": True}
        # ✅ CLAVE: si este webhook NO es el bot del ticket, IGNORAR para evitar duplicados
        if ticket_bot_key != bot_key:
            return {"ok": True}
        
        m = CHATID_RE.search(replied_text)
        if not m:
            send_message(bot_key, chat_id, "⚠️ No encontré ChatID en el ticket.")
            return {"ok": True}

        client_chat_id = int(m.group(1))

        # extraer respuesta
        reply_text = re.sub(r"^/r(@\w+)?\s*", "", text).strip()
        if not reply_text:
            send_message(bot_key, chat_id, "⚠️ Escribe algo después de /r. Ej: /r Ya te ayudamos.")
            return {"ok": True}

        # ✅ Como ticket_bot_key == bot_key, respondemos con ESTE bot
        send_message(bot_key, client_chat_id, f"🤓 Soporte: {reply_text}")
        send_message(bot_key, chat_id, f"✅ Respuesta enviada al cliente usando {bot_key}.")
        return {"ok": True}

    return {"ok": True}
