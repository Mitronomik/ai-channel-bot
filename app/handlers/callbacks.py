# -*- coding: utf-8 -*-
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from telegram.error import Forbidden, TelegramError # Импортируем ошибки
from telegram.constants import ParseMode # Для возможной разметки подписи

import io # Нужен для InputFile из байтов

from .. import config
from ..post_logger import log_post # Импортируем функцию логирования
from ..openai_client import generate_image # Импортируем функцию генерации изображения
from ..utils import download_image # Импортируем функцию скачивания изображения

logger = logging.getLogger(__name__)

# --- Inline клавиатура для черновиков (без изменений) ---
INLINE_ACTION_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📤 Опубликовать", callback_data="publish"),
        InlineKeyboardButton("🗑 Удалить", callback_data="delete")
    ]
])

# ============================================================
# --- ОБНОВЛЕННЫЙ Обработчик нажатий на inline-кнопки ---
# ============================================================
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на inline-кнопки ('publish', 'delete')."""
    query = update.callback_query
    if not query or not query.data:
        logger.warning("Получен пустой callback_query или query.data")
        return

    # Проверяем, что пользователь - админ
    if query.from_user.id != config.ADMIN_ID:
        try:
            # Отвечаем на коллбэк с сообщением об ошибке
            await query.answer("🚫 Доступ запрещен.", show_alert=True)
        except TelegramError as e:
            logger.error(f"Ошибка ответа на callback неавторизованного пользователя: {e}")
        return

    # Отвечаем на колбэк, чтобы кнопка перестала "грузиться"
    try:
        await query.answer()
    except TelegramError as e:
        logger.warning(f"Не удалось ответить на callback_query: {e}")


    # --- Логика для кнопки "Опубликовать" ---
    if query.data == "publish":
        if not query.message:
             logger.error("Не удалось получить сообщение из callback_query для публикации.")
             # Уведомить админа об ошибке?
             await ctx.bot.send_message(config.ADMIN_ID, "❌ Ошибка: Не удалось получить текст исходного сообщения для публикации.")
             return

        original_message_text = query.message.text # Текст сообщения, к которому прикреплена кнопка
        if not original_message_text:
             logger.error("Сообщение, к которому прикреплена кнопка 'Опубликовать', не содержит текста.")
             await query.edit_message_text("❌ Ошибка: Не удалось прочитать текст черновика.")
             return

        # 1. Извлекаем текст поста из сообщения с черновиком
        text_to_publish = original_message_text
        # Список возможных префиксов, которые нужно удалить
        prefixes_to_remove = [
            "💡 Черновик:", "📰 Новость:", "⚙️ Автопост:",
            "💡 Черновик (Perplexity):", "⚠️ Использована резервная модель" # Удаляем и предупреждение о модели
        ]
        # Ищем и удаляем первый найденный префикс (и возможные переносы строк после него)
        for prefix in prefixes_to_remove:
             # Ищем префикс с возможным двоеточием или без, и с новой строки
             if text_to_publish.strip().startswith(prefix.strip()):
                 # Удаляем префикс и все до первого осмысленного символа после него
                 parts = text_to_publish.split(prefix.strip(), 1)
                 if len(parts) > 1:
                     text_to_publish = parts[1].strip()
                 else: # Если вдруг префикс был всем сообщением
                     text_to_publish = ""
                 break # Убираем только первый найденный префикс

        # Проверяем, что текст не пуст после удаления префиксов
        if not text_to_publish:
             logger.warning("Попытка опубликовать пустой текст после удаления префикса.")
             await query.edit_message_text("⚠️ Не удалось извлечь текст для публикации (текст пуст после удаления служебных префиксов).")
             return
        logger.info(f"Извлечен текст для публикации: '{text_to_publish[:100].replace(chr(10),' ')}...'")


        # 2. Пытаемся сгенерировать и скачать изображение, если включено
        image_bytes = None
        if config.IMAGE_GENERATION_ENABLED:
            # Уведомляем админа о начале генерации
            try:
                await query.edit_message_text("⏳ Генерирую изображение для поста...")
            except TelegramError as e:
                logger.warning(f"Не удалось обновить сообщение для админа перед генерацией изображения: {e}")

            try:
                # Используем извлеченный текст поста как промпт
                image_url = await generate_image(text_to_publish)

                if image_url:
                    # Пытаемся скачать изображение
                    image_bytes = await download_image(image_url)
                    if not image_bytes:
                        logger.warning("Не удалось скачать сгенерированное изображение по URL.")
                        try:
                             await query.edit_message_text("⚠️ Не удалось скачать картинку. Публикую только текст...")
                        except TelegramError as e: logger.warning(f"Не удалось обновить сообщение админа: {e}")
                        # image_bytes уже None
                    else:
                         logger.info("Изображение для поста успешно сгенерировано и скачано.")
                         # Не обновляем сообщение админа здесь, т.к. скоро будет финальный статус
                else:
                    logger.warning("Функция generate_image не вернула URL.")
                    try:
                         await query.edit_message_text("⚠️ Не удалось сгенерировать картинку (нет URL). Публикую только текст...")
                    except TelegramError as e: logger.warning(f"Не удалось обновить сообщение админа: {e}")
                    image_bytes = None # Убеждаемся

            except Exception as img_e:
                logger.error(f"Ошибка во время генерации или скачивания изображения: {img_e}", exc_info=True)
                try:
                    # Сообщаем об ошибке, но продолжаем публиковать текст
                    await query.edit_message_text(f"⚠️ Ошибка генерации картинки ({type(img_e).__name__}). Публикую только текст...")
                except TelegramError as e: logger.warning(f"Не удалось обновить сообщение админа: {e}")
                image_bytes = None # Убеждаемся, что публикуем только текст
        else:
             logger.info("Генерация изображений отключена, публикуется только текст.")


        # 3. Публикация в канал
        try:
            sent_message = None
            publication_type = "неизвестно" # Тип для логов и сообщения админу

            # Уведомляем админа о начале публикации
            try:
                status_msg = "⏳ Публикую фото с текстом..." if image_bytes else "⏳ Публикую текст..."
                await query.edit_message_text(status_msg)
            except TelegramError as e: logger.warning(f"Не удалось обновить сообщение админа перед публикацией: {e}")


            if image_bytes:
                # Отправляем ФОТО с текстом в ПОДПИСИ (caption)
                caption = text_to_publish
                # Проверяем лимит длины подписи (1024 символа в Telegram)
                caption_limit = 1024
                if len(caption) > caption_limit:
                    logger.warning(f"Текст поста ({len(caption)} симв.) длиннее лимита подписи ({caption_limit}). Текст будет обрезан.")
                    caption = caption[:caption_limit]

                sent_message = await ctx.bot.send_photo(
                    chat_id=config.CHANNEL_ID,
                    photo=io.BytesIO(image_bytes), # Передаем байты изображения
                    caption=caption,
                    # parse_mode можно добавить, если нужен Markdown/HTML в подписи
                    # parse_mode=ParseMode.MARKDOWN
                )
                publication_type = "фото с подписью"
            else:
                # Отправляем только ТЕКСТ
                sent_message = await ctx.bot.send_message(
                    chat_id=config.CHANNEL_ID,
                    text=text_to_publish
                    # parse_mode=ParseMode.MARKDOWN # Если нужен Markdown/HTML в тексте
                )
                publication_type = "текстовый пост"

            logger.info(f"Пост ({publication_type}) успешно отправлен в канал {config.CHANNEL_ID}, message_id={sent_message.message_id}")

            # 4. Логируем опубликованный пост в CSV (логируем полный текст)
            try:
                log_post(
                    message_id=sent_message.message_id,
                    text=text_to_publish, # Логируем ПОЛНЫЙ текст, не обрезанный caption
                    timestamp=sent_message.date, # Используем время отправки от Telegram
                    reactions=0 # Реакции на момент публикации неизвестны
                )
            except Exception as log_e:
                logger.error(f"❌ Ошибка логирования поста {sent_message.message_id} после публикации: {log_e}")
                # Уведомляем админа об этой проблеме, она не должна мешать публикации
                await ctx.bot.send_message(
                    chat_id=config.ADMIN_ID,
                    text=f"⚠️ Пост опубликован (ID: {sent_message.message_id}), но произошла ошибка при его записи в лог: {log_e}"
                )

            # 5. Редактируем исходное сообщение в чате с админом - финальный статус
            final_admin_text = f"✅ Опубликовано ({publication_type}) в канале!\n\n"
            preview_text = text_to_publish.replace('\n', ' ')[:100] # Краткий предпросмотр
            if image_bytes:
                 final_admin_text += f"🖼️ + _{preview_text}..._"
            else:
                 final_admin_text += f"_{preview_text}..._"

            try:
                 await query.edit_message_text(final_admin_text, parse_mode=ParseMode.MARKDOWN)
            except TelegramError as e:
                 logger.warning(f"Не удалось обновить финальный статус для админа: {e}")
                 # Можно отправить новым сообщением, если редактирование не удалось
                 # await ctx.bot.send_message(config.ADMIN_ID, final_admin_text, parse_mode=ParseMode.MARKDOWN)


        except Forbidden as e:
            # Ошибка прав доступа
            logger.error(f"❌ Forbidden: Не удалось опубликовать пост в канал {config.CHANNEL_ID}: {e}. Проверьте права бота в канале.", exc_info=True)
            error_text = f"❌ Ошибка прав доступа: {e}\nБот должен быть администратором канала с правом отправки "
            error_text += "фотографий." if image_bytes else "сообщений."
            try:
                await query.edit_message_text(error_text)
            except TelegramError as te: logger.error(f"Не удалось уведомить админа об ошибке Forbidden: {te}")

        except TelegramError as e:
             # Другие ошибки Telegram API
             logger.error(f"❌ Ошибка Telegram при публикации в канал {config.CHANNEL_ID}: {e}", exc_info=True)
             error_text = f"❌ Ошибка Telegram при публикации: {e}"
             try:
                 await query.edit_message_text(error_text)
             except TelegramError as te: logger.error(f"Не удалось уведомить админа об ошибке Telegram: {te}")

        except Exception as e:
            # Любая другая непредвиденная ошибка
            logger.error(f"❌ Непредвиденная ошибка при публикации в канал {config.CHANNEL_ID}: {e}", exc_info=True)
            error_text = f"❌ Непредвиденная ошибка при публикации: {e}"
            try:
                await query.edit_message_text(error_text)
            except TelegramError as te: logger.error(f"Не удалось уведомить админа о непредвиденной ошибке: {te}")


    # --- Логика для кнопки "Удалить" ---
    elif query.data == "delete":
        try:
            await query.edit_message_text("🗑 Черновик удален.")
            logger.info(f"Черновик удален пользователем {query.from_user.id}")
        except TelegramError as e:
            logger.warning(f"Не удалось отредактировать сообщение для удаления черновика: {e}")

    # --- Обработка неизвестных callback_data ---
    else:
        logger.warning(f"Получен неизвестный callback_data: '{query.data}' от пользователя {query.from_user.id}")
        try:
            # Можно просто ответить, не меняя сообщение
            await query.answer("Неизвестное действие.")
        except TelegramError as e:
            logger.warning(f"Не удалось ответить на неизвестный callback_query: {e}")

# --- Создаем хэндлер для колбэков ---
callback_handler = CallbackQueryHandler(handle_callback)
