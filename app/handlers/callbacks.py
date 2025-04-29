import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from telegram.error import Forbidden # Импортируем ошибку
from .. import config
from ..post_logger import log_post # Импортируем функцию логирования

logger = logging.getLogger(__name__)

# --- Inline клавиатура (переносим сюда для согласованности) ---
INLINE_ACTION_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📤 Опубликовать", callback_data="publish"),
        InlineKeyboardButton("🗑 Удалить", callback_data="delete")
    ]
])

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на inline-кнопки."""
    query = update.callback_query
    # Проверяем, что пользователь - админ
    if query.from_user.id != config.ADMIN_ID:
        await query.answer("🚫 Доступ запрещен.", show_alert=True)
        return

    await query.answer() # Отвечаем на колбэк, чтобы кнопка перестала "грузиться"
    original_message_text = query.message.text # Текст сообщения, к которому прикреплена кнопка

    if query.data == "publish":
        # Извлекаем текст поста из сообщения с черновиком
        # Убираем префикс "💡 Черновик:" или похожий
        text_to_publish = original_message_text
        prefixes_to_remove = ["💡 Черновик:", "📰 Новость:", "⚙️ Автопост:"]
        for prefix in prefixes_to_remove:
             if text_to_publish.startswith(prefix):
                 text_to_publish = text_to_publish.split(prefix, 1)[1].strip()
                 break # Убираем только первый найденный префикс

        if not text_to_publish:
             logger.warning("Попытка опубликовать пустой текст после удаления префикса.")
             await query.edit_message_text("⚠️ Не удалось извлечь текст для публикации.")
             return

        try:
            # Отправляем сообщение в КАНАЛ
            sent_message = await ctx.bot.send_message(
                chat_id=config.CHANNEL_ID,
                text=text_to_publish
            )
            logger.info(f"Пост успешно отправлен в канал {config.CHANNEL_ID}, message_id={sent_message.message_id}")

            # Логируем опубликованный пост в CSV
            try:
                log_post(
                    message_id=sent_message.message_id,
                    text=text_to_publish,
                    timestamp=sent_message.date # Используем время отправки от Telegram
                )
            except Exception as log_e:
                logger.error(f"❌ Ошибка логирования поста {sent_message.message_id} после публикации: {log_e}")
                # Не прерываем основной процесс, но сообщаем об ошибке

            # Редактируем исходное сообщение в чате с админом
            await query.edit_message_text(f"✅ Опубликовано в канале!\n\n{text_to_publish[:100]}...") # Показываем часть текста для ясности

        except Forbidden as e:
            logger.error(f"❌ Не удалось опубликовать пост в канал {config.CHANNEL_ID}: {e}. Проверьте права бота в канале.", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка публикации: {e}\nБот должен быть администратором канала с правом отправки сообщений.")
        except Exception as e:
            logger.error(f"❌ Непредвиденная ошибка при публикации в канал {config.CHANNEL_ID}: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Ошибка публикации: {e}")

    elif query.data == "delete":
        await query.edit_message_text("🗑 Черновик удален.")
        logger.info(f"Черновик удален пользователем {query.from_user.id}")

# Создаем хэндлер для колбэков
callback_handler = CallbackQueryHandler(handle_callback)
