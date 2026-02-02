import re
import os
import requests
from fastapi import FastAPI, Request, HTTPException
from openpyxl import load_workbook



# =========================================================
# CONFIG
# =========================================================
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "-1003575621343"))  # tu grupo soporte

BOTS = {
    "bot_a": {
        "token_env": "BOT_TOKEN_A",
        "display": "Ayuda Cajero Referidor",
        "default_error_code": "300",
        "default_error_text": "Error 300, Metabet requiere biométrico",
    },
    "bot_b": {
        "token_env": "BOT_TOKEN_B",
        "display": "Bot Pruebas",
        "default_error_code": "601",
        "default_error_text": "Error 601, la página necesita biométrico",
    },
    "bot_c": {
        "token_env": "BOT_TOKEN_C",
        "display": "HS Call Center",
        "default_error_code": "601",
        "default_error_text": "Error 601, la página necesita biométrico",
    },
}

# =========================================================
# APP
# =========================================================
app = FastAPI()

CHATID_RE = re.compile(r"ChatID:\s*(-?\d+)")
TICKET_TAG = "🧾 TICKET"
ERROR_CATALOG_PATH = os.getenv("ERROR_CATALOG_PATH", "catalogo_errores.xlsx")
ERROR_MAP = {}  # cache: "604" -> {"plataforma":..., "causa":..., "solucion":...}
SUPPORT_ROUTER_BOT_KEY = os.getenv("SUPPORT_ROUTER_BOT_KEY", "bot_a").strip()
TICKET_API_KEY = os.getenv("TICKET_API_KEY", "").strip()

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

def send_message(
    bot_key: str,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    reply_markup: dict | None = None
):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg(bot_key, "sendMessage", payload)

def answer_callback_query(bot_key: str, callback_query_id: str, text: str = "", show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}
    return tg(bot_key, "answerCallbackQuery", payload)

# ✅ NUEVO: quitar teclado (botón) del mensaje original
def remove_inline_keyboard(bot_key: str, chat_id: int, message_id: int):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},  # vacío = desaparece
    }
    return tg(bot_key, "editMessageReplyMarkup", payload)

def parse_ticket_botkey(text: str) -> str | None:
    m = re.search(r"BotKey:\s*([a-zA-Z0-9_]+)", text)
    return m.group(1) if m else None

# =========================================================
# Health
# =========================================================


def require_api_key(req: Request):
    if not TICKET_API_KEY:
        return  # si no seteas API key, queda abierto (no recomendado)
    key = req.headers.get("x-api-key", "").strip()
    if key != TICKET_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.post("/ticket")
async def create_ticket(req: Request):
    require_api_key(req)

    payload = await req.json()

    # esperado:
    # {
    #   "bot_key": "bot_c",
    #   "error_code": "604",
    #   "user": {"id":123, "first_name":"", "last_name":"", "username":""}
    # }

    bot_key = (payload.get("bot_key") or "").strip()
    error_code = str(payload.get("error_code") or "").strip()
    user = payload.get("user") or {}

    if bot_key not in BOTS:
        raise HTTPException(status_code=400, detail="bot_key no registrado en BOTS")
    if not error_code:
        raise HTTPException(status_code=400, detail="error_code requerido")
    if not user.get("id"):
        raise HTTPException(status_code=400, detail="user.id (chatid) requerido")

    client_chat_id = int(user["id"])
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    username = (user.get("username") or "").strip()
    uname = f"@{username}" if username else "(sin username)"
    full_name = (first + " " + last).strip() or "Cliente"

    bot_display = BOTS[bot_key]["display"]

    # ✅ catálogo excel
    ensure_error_map_loaded()
    info = ERROR_MAP.get(error_code, {"plataforma": "-", "causa": "-", "solucion": "-"})

    ticket_text = (
        f"{TICKET_TAG}\n"
        f"🤖 Bot: {bot_display}\n"
        f"BotKey: {bot_key}\n"
        f"👤 Cliente: {full_name} {uname}\n"
        f"ChatID: {client_chat_id}\n"
        f"⚠️ Error: Error {error_code}\n"
        f"📝 Plataforma: {info['plataforma']}\n"
        f"🧩 Causa: {info['causa']}\n"
        f"✅ Solución: {info['solucion']}\n\n"
        f"↩️ Responde a ESTE mensaje con:\n"
        f"/r tu respuesta aquí"
    )

    # ✅ IMPORTANTE: enviamos el ticket al grupo usando el BOT ROUTER
    send_message(SUPPORT_ROUTER_BOT_KEY, SUPPORT_GROUP_ID, ticket_text)

    return {"ok": True}


@app.get("/")
def health():
    return {"ok": True, "service": "telegram-support-multibot", "bots": list(BOTS.keys())}


def load_error_catalog(path: str) -> dict:
    wb = load_workbook(path, data_only=True)
    ws = wb.active

    out = {}
    # headers fila 1, datos desde fila 2, A-D
    for row in ws.iter_rows(min_row=2, max_col=4, values_only=True):
        code, plataforma, causa, solucion = row
        if code is None:
            continue

        code_str = str(code).strip()
        if not code_str:
            continue

        out[code_str] = {
            "plataforma": (str(plataforma).strip() if plataforma else "-"),
            "causa": (str(causa).strip() if causa else "-"),
            "solucion": (str(solucion).strip() if solucion else "-"),
        }

    return out

def ensure_error_map_loaded():
    global ERROR_MAP
    if not ERROR_MAP:
        try:
            ERROR_MAP = load_error_catalog(ERROR_CATALOG_PATH)
        except Exception:
            ERROR_MAP = {}



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

        if data.startswith("report:"):
            error_code = data.split(":", 1)[1].strip()

            from_user = cq.get("from", {})
            msg = cq.get("message", {})
            chat = msg.get("chat", {})

            client_chat_id = chat.get("id")
            client_message_id = msg.get("message_id")  # ✅ para editar y quitar botón

            full_name = (from_user.get("first_name", "") + " " + from_user.get("last_name", "")).strip()
            username = from_user.get("username")
            uname = f"@{username}" if username else "(sin username)"
            
            ensure_error_map_loaded()
            info = ERROR_MAP.get(str(error_code).strip(), {"plataforma": "-", "causa": "-", "solucion": "-"})

            ticket_text = (
                f"{TICKET_TAG}\n"
                f"🤖 Bot: {bot_display}\n"
                f"BotKey: {bot_key}\n"
                f"👤 Cliente: {full_name} {uname}\n"
                f"ChatID: {client_chat_id}\n"
                f"⚠️ Error: Error {error_code}\n"
                f"📝 Plataforma: {info['plataforma']}\n"
                f"🧩 Causa: {info['causa']}\n"
                f"✅ Solución: {info['solucion']}\n\n"
                f"↩️ Responde a ESTE mensaje con:\n"
                f"/r tu respuesta aquí"
            )


            # ✅ PROBLEMA 1: quitar el botón para evitar doble ticket
            # (si falla por cualquier cosa, igual seguimos)
            try:
                remove_inline_keyboard(bot_key, client_chat_id, client_message_id)
            except Exception:
                pass

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

        if text.startswith("/start"):
            send_message(bot_key, chat_id, f"Hola 👋\nUsa /prueba para ver el error con el botón REPORTAR.\nBot: {bot_display}")
            return {"ok": True}

        if text.startswith("/prueba"):
            err_text = BOTS[bot_key]["default_error_text"]
            err_code = BOTS[bot_key]["default_error_code"]
            kb = {"inline_keyboard": [[{"text": "📩 REPORTAR", "callback_data": f"report:{err_code}"}]]}
            send_message(bot_key, chat_id, err_text, reply_markup=kb)
            return {"ok": True}

        if text.startswith("/getchatid"):
            send_message(bot_key, chat_id, f"chat_id de este chat/grupo: {chat_id}")
            return {"ok": True}

        # /r (solo en el grupo soporte y como reply a ticket)
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

            # ✅ PROBLEMA 2 y 3: si este webhook NO es del bot dueño del ticket, IGNORAR.
            # Esto evita confirmación doble y evita enviar 2 veces al cliente.
            if bot_key != SUPPORT_ROUTER_BOT_KEY:
                return {"ok": True}

            m = CHATID_RE.search(replied_text)
            if not m:
                send_message(bot_key, chat_id, "⚠️ No encontré ChatID en el ticket.")
                return {"ok": True}

            client_chat_id = int(m.group(1))

            reply_text = re.sub(r"^/r(@\w+)?\s*", "", text).strip()
            if not reply_text:
                send_message(bot_key, chat_id, "⚠️ Escribe algo después de /r. Ej: /r Ya te ayudamos con el biométrico.")
                return {"ok": True}

            # RESPONDER al cliente usando el bot correcto del ticket
            send_message(ticket_bot_key, client_chat_id, f"🤓 Soporte: {reply_text}")
            send_message(bot_key, chat_id, f"✅ Respuesta enviada al cliente usando {ticket_bot_key}.")
            return {"ok": True}

    return {"ok": True}

