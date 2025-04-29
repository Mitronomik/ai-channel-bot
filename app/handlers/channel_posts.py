import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from .. import config
from ..post_logger import log_post

logger = logging.getLogger(__name__)

async def log_new_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Логирует новый пост, опубликованный в канале (любым способом)."""
    if not update.channel_post:
        return # Игнорируем другие типы апдейтов

    message = update.channel_post
    # Убедимся, что это именно наш канал (хотя фильтр уже должен это делать)
    if message.chat_id != config.CHANNEL_ID:
        logger.warning(f"Получен channel_post из другого канала: {message.chat_id}")
        return

    # Логируем только текстовые сообщения (можно расширить для фото и т.д.)
    if message.text:
        logger.info(f"Обнаружен новый пост в канале {config.CHANNEL_ID} (message_id={message.message_id}). Логирование...")
        try:
            log_post(
                message_id=message.message_id,
                text=message.text,
                timestamp=message.date,
                reactions=0 # Реакции на момент публикации неизвестны
            )
        except Exception as e:
            logger.error(f"❌ Ошибка логирования поста {message.message_id} из канала: {e}", exc_info=True)
    else:
        logger.debug(f"Получен нетекстовый пост (message_id={message.message_id}) в канале, логирование пропущено.")

# Фильтр для новых постов в НАШЕМ канале
# Важно: Бот должен быть добавлен в канал как администратор с правами читать сообщения!
channel_post_filter = filters.UpdateType.CHANNEL_POST & filters.Chat(chat_id=config.CHANNEL_ID)

# Создаем хэндлер
channel_post_handler = MessageHandler(channel_post_filter, log_new_channel_post)

# Также можно добавить обработчик для ИЗМЕНЕННЫХ постов, если нужно обновлять текст/реакции
# async def log_edited_channel_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
#     # ... (логика похожа, но обновляет существующую запись) ...
# edited_channel_post_handler = MessageHandler(filters.UpdateType.EDITED_CHANNEL_POST & filters.Chat(chat_id=config.CHANNEL_ID), log_edited_channel_post)

# Примечание: Получение РЕАКЦИЙ на посты канала через стандартный Bot API затруднено.
# Обычно для этого требуются либо user-боты, либо специальные библиотеки/сервисы,
# либо ручное обновление через команду бота. В данном коде реакции логгируются как 0
# при автоматическом обнаружении поста. Топ постов будет работать, если реакции
# обновляются каким-либо другим способом (например, вручную).
