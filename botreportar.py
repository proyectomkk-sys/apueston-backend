# bot.py
# Requisitos:
#   pip install -U python-telegram-bot
#
# Ejecutar:
#   python bot.py

import json
import time
import re
import logging
import html
import os
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Falta BOT_TOKEN en variables de entorno")

# Grupo donde llegan los tickets
TICKETS_GROUP_ID = -1003575621343

# MiniApp principal (GitHub Pages / hosting)
WEBAPP_APUESTON_URL = "https://proyectomkk-sys.github.io/BotSoporteMKK/apueston.html"
WEBAPP_SOPORTE_CAJEROS_URL = "https://proyectomkk-sys.github.io/BotSoporteMKK/soportecajeros2.html"

CB_BOT_CAJEROS = "bot_cajeros"

# Callbacks
CB_FAIL_MSG = "fail_msg_601"
CB_REPORT_601 = "report_601"

# Mapa: message_id del ticket en el grupo -> user_id del cliente
# (si el bot creó el ticket desde sendData, se llena; si el ticket vino del backend, quizá no)
TICKET_MAP: dict[int, int] = {}


# ─────────────────────────────
# MENSAJE DE BIENVENIDA + BOTÓN
# ─────────────────────────────
def welcome_text() -> str:
    return (
        "Bienvenido al servicio automatizado de recargas.\n"
        "Seleccione la plataforma que desea cargar:"
    )

def main_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="🅰️ Apueston",
                    web_app=WebAppInfo(url=WEBAPP_APUESTON_URL),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚨 Mensaje de falla",
                    callback_data=CB_FAIL_MSG,
                )
            ],
            [
                InlineKeyboardButton(
                    text="BOT 💀",
                    callback_data=CB_BOT_CAJEROS,
                )
            ],
        ]
    )



# ─────────────────────────────
# /start
# ─────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        welcome_text(),
        reply_markup=main_inline_keyboard()
    )


# ─────────────────────────────
# CUALQUIER TEXTO EN PRIVADO → MISMO MENSAJE QUE /start
# ─────────────────────────────
async def welcome_on_any_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Solo en privado
    if update.effective_chat.type != "private":
        return

    # Ignorar comandos
    if not update.message or not update.message.text:
        return
    if update.message.text.startswith("/"):
        return

    await start(update, context)


# ─────────────────────────────
# UTILIDAD: extraer user_id del texto del ticket
# ─────────────────────────────
def extract_user_id_from_ticket(text: str) -> Optional[int]:
    """
    Busca patrones como:
      id:123456
      id: 123456
      id:1234567890
    """
    if not text:
        return None
    m = re.search(r"\bid\s*:\s*(\d{5,20})\b", text, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))

def bot_cajeros_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                text="🔎 Intenta verificando",
                web_app=WebAppInfo(url=WEBAPP_SOPORTE_CAJEROS_URL),
            )
        ]
    ])

async def on_bot_cajeros(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # Solo privado
    if q.message.chat.type != "private":
        return

    await q.message.reply_text(
        "💀 El BOT Bet Cajeros 24/7 dejó de funcionar",
        reply_markup=bot_cajeros_keyboard()
    )


def report_601_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text="📩 Reportar", callback_data=CB_REPORT_601)]
    ])

async def on_fail_message_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # Solo en privado
    if q.message.chat.type != "private":
        return

    await q.message.reply_text(
        "Error 601. La página de Apuestón presenta fallas",
        reply_markup=report_601_keyboard()
    )

async def on_report_601(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user = q.from_user
    chat = q.message.chat

    user_id_chat = chat.id  # ESTE es el id que luego usas para responder por privado
    username = f"@{user.username}" if user.username else "sin_username"
    full_name = (f"{user.first_name or ''} {user.last_name or ''}").strip() or "Sin nombre"

    # Detalle del error
    causa = "Credenciales caducadas."
    solucion = "Verificar e ingresar nuevamente las credenciales del usuario."

    # Texto del ticket (HTML para evitar problemas con caracteres raros)
    ticket_text = (
        "🎫 <b>TICKET NUEVO</b>\n"
        f"👤 <b>Usuario:</b> {html.escape(full_name)} | {html.escape(username)}\n"
        f"🆔 <b>Chat ID:</b> <code>{user_id_chat}</code>\n\n"
        "⚠️ <b>Error:</b> 601\n"
        "📝 <b>Detalle:</b> La página de Apuestón presenta fallas\n\n"
        f"🔎 <b>Causa:</b> {html.escape(causa)}\n"
        f"✅ <b>Solución:</b> {html.escape(solucion)}"
    )

    sent = await context.bot.send_message(
        chat_id=TICKETS_GROUP_ID,
        text=ticket_text,
        parse_mode="HTML"
    )

    # Guardar el vínculo ticket -> chat id para que /r funcione
    TICKET_MAP[sent.message_id] = int(user_id_chat)

    await q.message.reply_text("✅ Reporte enviado a Soporte. Te responderemos por este chat.")



# ─────────────────────────────
# RECIBIR DATOS DESDE MINIAPP via tg.sendData (OPCIONAL)
# Si ya envías reportes 100% por backend, esto puede quedarse igual (no molesta).
# ─────────────────────────────
async def on_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    wad = msg.web_app_data
    if not wad:
        return

    logger.info("WEB_APP_DATA raw: %s", getattr(wad, "data", ""))

    try:
        payload = json.loads(wad.data)
    except Exception as e:
        logger.exception("JSON invalido en web_app_data")
        await msg.reply_text("❌ Error al procesar el reporte (JSON inválido).")
        return

    if payload.get("type") != "reporte_falla":
        logger.info("WEB_APP_DATA ignorado: type=%s", payload.get("type"))
        return

    user = payload.get("user") or {}
    desc = (payload.get("description") or "").strip()
    has_shot = bool(payload.get("hasScreenshot"))
    ts = int(payload.get("ts") or int(time.time() * 1000))

    user_id = user.get("id")
    if not user_id:
        await msg.reply_text("❌ Reporte inválido. Abre la miniapp desde Telegram (no desde el navegador).")
        return

    full_name = " ".join([str(user.get("first_name", "")), str(user.get("last_name", ""))]).strip() or "Sin nombre"
    username = user.get("username") or "sin_username"

    # ✅ Usar HTML + escape (evita que usernames con _ rompan Markdown)
    ticket_text = (
        "🎫 <b>TICKET NUEVO</b>\n"
        f"👤 <b>Usuario:</b> {html.escape(full_name)} | @{html.escape(username)}\n"
        f"🆔 <b>Chat ID:</b> <code>{int(user_id)}</code>\n"
        f"🕒 <b>Timestamp:</b> <code>{ts}</code>\n"
        f"📎 <b>Captura:</b> {'Sí' if has_shot else 'No'}\n\n"
        f"📝 <b>Descripción:</b>\n{html.escape(desc)}"
    )

    try:
        sent = await context.bot.send_message(
            chat_id=TICKETS_GROUP_ID,
            text=ticket_text,
            parse_mode="HTML"
        )

        # Guardamos ticket -> usuario para /r
        TICKET_MAP[sent.message_id] = int(user_id)

        await msg.reply_text("✅ Reporte enviado a soporte. Te responderemos por este chat.")
        logger.info("Ticket enviado al grupo. message_id=%s user_id=%s", sent.message_id, user_id)

    except Exception:
        logger.exception("No se pudo enviar el ticket al grupo")
        await msg.reply_text(
            "❌ Recibí tu reporte, pero no pude enviarlo al grupo de soporte.\n"
            "Revisa que el bot esté en el grupo y tenga permiso para escribir."
        )

    msg = update.effective_message
    wad = msg.web_app_data
    if not wad:
        return

    try:
        payload = json.loads(wad.data)
    except Exception:
        await msg.reply_text("❌ Error al procesar el reporte (JSON inválido).")
        return

    if payload.get("type") != "reporte_falla":
        return

    user = payload.get("user") or {}
    desc = (payload.get("description") or "").strip()
    has_shot = bool(payload.get("hasScreenshot"))
    ts = int(payload.get("ts") or time.time() * 1000)

    user_id = user.get("id")
    if not user_id:
        await msg.reply_text("❌ Reporte inválido. Abre la miniapp desde Telegram.")
        return

    full_name = " ".join([user.get("first_name", ""), user.get("last_name", "")]).strip() or "Sin nombre"
    username = user.get("username") or "sin_username"

    ticket_text = (
        "🎫 **TICKET NUEVO**\n"
        f"👤 **Usuario:** {full_name} | @{username} | id:{user_id}\n"
        f"🕒 **Timestamp:** `{ts}`\n"
        f"📎 **Captura:** {'Sí (pendiente backend)' if has_shot else 'No'}\n\n"
        f"📝 **Descripción:**\n{desc}"
    )

    sent = await context.bot.send_message(
        chat_id=TICKETS_GROUP_ID,
        text=ticket_text,
        parse_mode="Markdown"
    )

    # Guardamos ticket -> usuario (solo si el bot crea el ticket)
    TICKET_MAP[sent.message_id] = int(user_id)

    await msg.reply_text("✅ Reporte enviado. Te responderemos por este chat.")


# ─────────────────────────────
# RESPONDER TICKETS DESDE EL GRUPO
# Uso:
#   1) Responde (reply) al ticket y escribe: /r tu respuesta
#   2) Manual: /r 123456789 tu respuesta
# ─────────────────────────────
async def reply_to_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    # Solo en el grupo de tickets
    if msg.chat_id != TICKETS_GROUP_ID:
        return

    # Debe ser una respuesta (reply) a un ticket
    if not msg.reply_to_message:
        return

    # Ignorar comandos distintos a /r
    if msg.text and msg.text.startswith("/") and not msg.text.startswith("/r"):
        return

    # Texto del mensaje
    text = msg.text or ""
    text = text.strip()

    # Si viene con /r, lo quitamos
    if text.startswith("/r"):
        text = text[2:].strip()

    if not text:
        return

    # 1) Intento normal: mapa
    ticket_mid = msg.reply_to_message.message_id
    user_id = TICKET_MAP.get(ticket_mid)

    # 2) Extraer id del texto o caption del ticket
    if not user_id:
        user_id = extract_user_id_from_ticket(msg.reply_to_message.text or "")
        if not user_id:
            user_id = extract_user_id_from_ticket(
                getattr(msg.reply_to_message, "caption", "") or ""
            )

    # 3) Modo manual: primer token es id
    parts = text.split(maxsplit=1)
    if parts and parts[0].isdigit() and len(parts[0]) >= 5:
        user_id = int(parts[0])
        text = parts[1] if len(parts) > 1 else ""

    if not user_id or not text.strip():
        return

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🛠️ Soporte:\n{text.strip()}"
        )
        await msg.reply_text("✅ Respuesta enviada al usuario por privado.")
    except Exception:
        await msg.reply_text(
            "❌ No pude enviarle mensaje al usuario.\n"
            "• El usuario no inició chat con el bot\n"
            "• Bloqueó el bot"
        )


# ─────────────────────────────
# ERROR HANDLER (evita 'No error handlers are registered')
# ─────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling an update:", exc_info=context.error)


# ─────────────────────────────
# MAIN
# ─────────────────────────────
def main() -> None:
    
    app = Application.builder().token(TOKEN).build()

    # /start
    app.add_handler(CommandHandler("start", start))

    # Botones inline (callbacks)
    app.add_handler(CallbackQueryHandler(on_fail_message_button, pattern=f"^{CB_FAIL_MSG}$"))
    app.add_handler(CallbackQueryHandler(on_report_601, pattern=f"^{CB_REPORT_601}$"))
    app.add_handler(CallbackQueryHandler(on_bot_cajeros, pattern=f"^{CB_BOT_CAJEROS}$"))

    # Cualquier texto (no comando) en privado -> muestra bienvenida + botón
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, welcome_on_any_text))

    # Recibir data desde miniapps via tg.sendData (si lo usas)
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))

    # Responder tickets desde el grupo con /r (solo actuará en el grupo indicado)
    app.add_handler(MessageHandler(filters.TEXT | filters.COMMAND, reply_to_ticket))

    app.add_error_handler(error_handler)

    print("🤖 Bot iniciado correctamente...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
