import logging
import sys
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler, # Используется для фильтров в MessageHandler
    PicklePersistence, # Для сохранения/восстановления jobs планировщика
    Defaults
)
from telegram.constants import ParseMode
from pathlib import Path

# --- Настройка логирования ---
# Устанавливаем уровень логирования для сторонних библиотек ниже, чтобы не засорять вывод
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO) # Можно поставить WARNING для меньшего количества логов PTB
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# Настраиваем корневой логгер
logging.basicConfig(
    level=logging.INFO, # Основной уровень логирования для нашего кода
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout, # Вывод логов в stdout (хорошо для Docker)
    # filename='bot.log', # Можно настроить запись в файл
    # filemode='a'
)
logger = logging.getLogger(__name__)

# --- Импорт конфигурации и хэндлеров ---
try:
    from app import config # Импортируем после настройки логирования
    from app.handlers import commands, callbacks, messages, channel_posts # Импортируем пакеты с хэндлерами
except ValueError as e:
    logger.critical(f"Критическая ошибка конфигурации: {e}")
    sys.exit(1) # Выход, если конфигурация неверна
except ImportError as e:
     logger.critical(f"Ошибка импорта модулей: {e}. Убедитесь, что все зависимости установлены и структура проекта верна.")
     sys.exit(1)

def main() -> None:
    """Запускает бота."""
    logger.info("🚀 Инициализация бота...")

    # --- Настройка Defaults ---
    # Можно задать parse_mode по умолчанию для всех сообщений бота
    defaults = Defaults(parse_mode=ParseMode.MARKDOWN)

    # --- Настройка Persistence ---
    # Используем PicklePersistence для сохранения состояния JobQueue между перезапусками
    # Файл будет сохранен в директории data/, которая монтируется из хоста
    persistence_path = config.DATA_DIR / 'bot_persistence.pickle'
    persistence = PicklePersistence(filepath=persistence_path)
    logger.info(f"Используется сохранение состояния в: {persistence_path}")


    # --- Сборка приложения ---
    try:
        application = (
            ApplicationBuilder()
            .token(config.BOT_TOKEN)
            .defaults(defaults)
            .persistence(persistence) # Добавляем persistence
            .read_timeout(30) # Увеличиваем таймауты для ожидания сети/API
            .get_updates_read_timeout(30)
            .connect_timeout(30)
            .write_timeout(30)
            .pool_timeout(30)
            .build()
        )
    except Exception as e:
        logger.critical(f"❌ Не удалось собрать приложение Telegram Bot: {e}", exc_info=True)
        sys.exit(1)


    # --- Регистрация хэндлеров ---
    # Команды (доступны только админу через фильтр в самих командах)
    for handler in commands.command_handlers:
        application.add_handler(handler)
        logger.debug(f"Добавлен обработчик команды: {handler.commands}")

    # Обработчик текстовых сообщений от админа (для кнопок меню)
    application.add_handler(messages.text_menu_handler)
    logger.debug("Добавлен обработчик текстового меню админа.")

    # Обработчик нажатий на inline-кнопки (проверка админа внутри)
    application.add_handler(callbacks.callback_handler)
    logger.debug("Добавлен обработчик inline-кнопок.")

    # Обработчик новых постов в канале (опционально, для логирования)
    # Убедитесь, что бот - админ канала с правом читать сообщения!
    application.add_handler(channel_posts.channel_post_handler)
    logger.info("Добавлен обработчик новых постов в канале (для логирования).")
    # application.add_handler(channel_posts.edited_channel_post_handler) # Если нужен и для измененных

    # --- Запуск бота ---
    logger.info(f"🤖 Бот запускается... Используется модель OpenAI: {config.MODEL}")
    if config.OPENAI_PROXY:
        logger.info(f"🔌 Прокси OpenAI: {config.OPENAI_PROXY}")
    else:
        logger.info("🔌 Прокси OpenAI: не используется")

    # Запускаем в режиме опроса (polling)
    # allowed_updates можно уточнить, чтобы бот получал только нужные типы обновлений
    allowed_updates = [
        Update.MESSAGE, Update.CALLBACK_QUERY, Update.CHANNEL_POST, Update.EDITED_CHANNEL_POST
    ]
    application.run_polling(allowed_updates=allowed_updates, drop_pending_updates=True) # drop_pending_updates=True - чтобы не обрабатывать старые сообщения после перезапуска

    logger.info("🏁 Бот остановлен.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
         # Логируем критические ошибки верхнего уровня, которые могли не пойматься внутри main
         logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА ВНЕ main(): {e}", exc_info=True)
         sys.exit(1) # Завершаем с ошибкой
