import re
import os
import requests
from fastapi import FastAPI, Request, HTTPException

# =========================================================
# CONFIG
# =========================================================
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "-1003575621343"))  # tu grupo soporte

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
    bot_display = BOTS[bot_key]["display"]

    # ----------------------------
    # 1) CallbackQuery: botón REPORTAR
    # ----------------------------
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        callback_id = cq.get("id")

        # data esperado: "report:300"
        if data.startswith("report:"):
            error_code = data.split(":", 1)[1].strip()

            from_user = cq.get("from", {})
            msg = cq.get("message", {})
            chat = msg.get("chat", {})  # chat donde se presionó el botón (cliente)

            client_chat_id = chat.get("id")
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

            # enviamos al grupo soporte
            send_message(bot_key, SUPPORT_GROUP_ID, ticket_text)

            # confirmación al cliente
            send_message(bot_key, client_chat_id, "✅ Tu reporte fue enviado. En breve soporte se comunicará contigo.")

            # cerramos callback
            answer_callback_query(bot_key, callback_id, "Reporte enviado ✅", show_alert=False)

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
        if chat_id == SUPPORT_GROUP_ID and (text.startswith("/r") or text.startswith("/r@")):
            reply_to = msg.get("reply_to_message")
            if not reply_to or not (reply_to.get("text") or ""):
                send_message(bot_key, chat_id, "⚠️ Debes responder (reply) al mensaje del ticket y escribir: /r tu respuesta")
                return {"ok": True}

            replied_text = (reply_to.get("text") or "")
            if TICKET_TAG not in replied_text:
                send_message(bot_key, chat_id, "⚠️ El mensaje respondido no parece un ticket.")
                return {"ok": True}

            # Detectar a qué bot pertenece ese ticket
            ticket_bot_key = parse_ticket_botkey(replied_text) or bot_key
            if ticket_bot_key not in BOTS:
                send_message(bot_key, chat_id, "⚠️ No pude determinar el bot del ticket (BotKey inválido).")
                return {"ok": True}

            m = CHATID_RE.search(replied_text)
            if not m:
                send_message(bot_key, chat_id, "⚠️ No encontré ChatID en el ticket.")
                return {"ok": True}

            client_chat_id = int(m.group(1))

            # extraer respuesta (quita "/r" o "/r@bot")
            reply_text = re.sub(r"^/r(@\w+)?\s*", "", text).strip()
            if not reply_text:
                send_message(bot_key, chat_id, "⚠️ Escribe algo después de /r. Ej: /r Ya te ayudamos con el biométrico.")
                return {"ok": True}

            # RESPONDER al cliente usando el bot correcto del ticket
            send_message(ticket_bot_key, client_chat_id, f"🤓 Soporte: {reply_text}")
            send_message(bot_key, chat_id, f"✅ Respuesta enviada al cliente usando {ticket_bot_key}.")
            return {"ok": True}

    return {"ok": True}