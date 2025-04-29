import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from .. import config
# Импортируем функции-обработчики команд, которые вызываются из меню
from .commands import (
    generate_idea,
    generate_news_post,
    show_stats,
    set_auto_post_best_time,
    weekly_report,
    research_perplexity,
    show_schedule,
    stop_auto_post,
)

logger = logging.getLogger(__name__)

# --- Обработка нажатий на кнопки ReplyKeyboard ---
async def handle_text_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения, соответствующие кнопкам меню."""
    # Этот хэндлер должен быть защищен фильтром filters.Chat(config.ADMIN_ID) при добавлении в Application
    user_id = update.effective_user.id
    if user_id != config.ADMIN_ID:
        logger.warning(f"Неавторизованная попытка использования текстового меню от user_id: {user_id}")
        return # Игнорируем сообщения не от админа

    txt = update.message.text.strip().lower()
    logger.info(f"Админ ({user_id}) нажал кнопку меню: {update.message.text}")

    # Сопоставляем текст кнопки с функцией-обработчиком команды
    if "💡 идея" in txt:
        await generate_idea(update, ctx)
    elif "📰 новости" in txt:
        await generate_news_post(update, ctx)
    elif "📊 статистика" in txt:
        await show_stats(update, ctx)
    elif "🕒 авто по лучшему" in txt: # Обновленный текст кнопки
        await set_auto_post_best_time(update, ctx)
    elif "📅 отчёт за неделю" in txt or "отчет" in txt:
        await weekly_report(update, ctx)
    elif "🔍 ресёрч pplx" in txt: # Обновленный текст кнопки
        # Вызываем research_perplexity без аргументов (будет использован дефолтный запрос)
        ctx.args = [] # Очищаем args на всякий случай
        await research_perplexity(update, ctx)
    elif "⚙️ расписание" in txt: # Обновленный текст кнопки
        await show_schedule(update, ctx)
    elif "🛑 остановить автопост" in txt: # Обновленный текст кнопки
        await stop_auto_post(update, ctx)
    else:
        # Если текст не соответствует ни одной кнопке, можно или ничего не делать,
        # или отправить сообщение о помощи/неизвестной команде
        logger.debug(f"Получен текст от админа, не соответствующий кнопкам меню: {txt}")
        # await update.message.reply_text("Не распознал эту команду. Используй кнопки меню или известные команды.")
        pass # Просто игнорируем

# --- Фильтр для текстовых сообщений только от админа ---
admin_text_filter = filters.TEXT & ~filters.COMMAND & filters.Chat(config.ADMIN_ID)

# --- Создаем хэндлер ---
text_menu_handler = MessageHandler(admin_text_filter, handle_text_menu)
