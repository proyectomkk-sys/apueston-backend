# app.py
import re
import requests
from fastapi import FastAPI, Request

# =========================================================
# CONFIG (SIN ENV LOCALES): PEGA TUS DATOS AQUÍ
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID = -1003575621343  # PEGA AQUI EL CHAT_ID REAL DEL GRUPO (empieza con -100)
BOT_DISPLAY_NAME = "Soporte BotMakers"  # nombre bonito para tickets (opcional)

# =========================================================
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
app = FastAPI()

ERROR_TEXT = "Error 300, Metabet requiere biométrico"

CHATID_RE = re.compile(r"ChatID:\s*(-?\d+)")
TICKET_TAG = "🧾 TICKET"

def tg(method: str, payload: dict):
    r = requests.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error calling {method}: {data}")
    return data["result"]

def send_message(chat_id: int, text: str, reply_to_message_id: int | None = None, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("sendMessage", payload)

def answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}
    return tg("answerCallbackQuery", payload)

@app.get("/")
def health():
    return {"ok": True, "service": "telegram-support-bot"}

@app.post("/telegram")
async def telegram_webhook(req: Request):
    update = await req.json()

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

            # armamos ticket
            uname = f"@{username}" if username else "(sin username)"
            ticket_text = (
                f"{TICKET_TAG}\n"
                f"🤖 Bot: {BOT_DISPLAY_NAME}\n"
                f"👤 Cliente: {full_name} {uname}\n"
                f"ChatID: {client_chat_id}\n"
                f"⚠️ Error: Error {error_code}, Metabet requiere biométrico\n"
                f"📝 Detalle: -\n"
                f"🧩 Causa: -\n"
                f"✅ Solución: -\n\n"
                f"↩️ Responde a ESTE mensaje con:\n"
                f"/r tu respuesta aquí"
            )

            # enviamos al grupo soporte
            send_message(SUPPORT_GROUP_ID, ticket_text)

            # confirmación al cliente
            send_message(client_chat_id, "✅ Tu reporte fué enviado! En breves soporte se comunicará contigo.")

            # cerramos callback
            answer_callback_query(callback_id, "Reporte enviado ✅", show_alert=False)

        return {"ok": True}

    # ----------------------------
    # 2) Mensajes (comandos /start /prueba /getchatid y /r en grupo)
    # ----------------------------
    if "message" in update:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        text = msg.get("text", "") or ""

        # /start
        if text.startswith("/start"):
            send_message(chat_id, "Hola. Usa /prueba para ver el error con el botón REPORTAR.")
            return {"ok": True}

        # /prueba (cliente)
        if text.startswith("/prueba"):
            kb = {
                "inline_keyboard": [
                    [{"text": "REPORTAR", "callback_data": "report:300"}]
                ]
            }
            send_message(chat_id, ERROR_TEXT, reply_markup=kb)
            return {"ok": True}

        # /getchatid (útil para sacar el id del grupo o chats)
        if text.startswith("/getchatid"):
            send_message(chat_id, f"chat_id de este chat/grupo: {chat_id}")
            return {"ok": True}

        # /r (solo en el grupo soporte y como reply a ticket)
        # Nota: Telegram puede mandar "/r@TuBot" también
        if chat_id == SUPPORT_GROUP_ID and (text.startswith("/r") or text.startswith("/r@")):
            reply_to = msg.get("reply_to_message")
            if not reply_to or not (reply_to.get("text") or ""):
                send_message(chat_id, "⚠️ Debes responder (reply) al mensaje del ticket y escribir: /r tu respuesta")
                return {"ok": True}

            replied_text = reply_to.get("text", "")
            if TICKET_TAG not in replied_text:
                send_message(chat_id, "⚠️ El mensaje respondido no parece un ticket.")
                return {"ok": True}

            m = CHATID_RE.search(replied_text)
            if not m:
                send_message(chat_id, "⚠️ No encontré ChatID en el ticket.")
                return {"ok": True}

            client_chat_id = int(m.group(1))

            # extraer respuesta
            # quita "/r" o "/r@bot"
            reply_text = text
            reply_text = re.sub(r"^/r(@\w+)?\s*", "", reply_text).strip()

            if not reply_text:
                send_message(chat_id, "⚠️ Escribe algo después de /r. Ej: /r Ya te ayudamos con el biométrico.")
                return {"ok": True}

            send_message(client_chat_id, f"📩 Soporte: {reply_text}")
            send_message(chat_id, "✅ Respuesta enviada al cliente.")
            return {"ok": True}


    return {"ok": True}
