import os
import logging
from dotenv import load_dotenv
from pathlib import Path

# Определяем базовую директорию проекта
# BASE_DIR = Path(__file__).resolve().parent.parent # Если config.py в корне app/
APP_DIR = Path(__file__).resolve().parent # Директория app/
BASE_DIR = APP_DIR.parent # Корень проекта

# Загружаем переменные окружения из файла .env в корне проекта
load_dotenv(BASE_DIR / '.env')

logger = logging.getLogger(__name__)

def get_env_var(var_name: str, default: str | None = None, required: bool = False, is_int: bool = False) -> str | int | None:
    """Получает переменную окружения, логирует и проверяет обязательность."""
    value = os.getenv(var_name, default)
    if required and value is None:
        logger.error(f"❌ Обязательная переменная окружения {var_name} не установлена!")
        raise ValueError(f"Переменная окружения {var_name} должна быть установлена.")
    if value is not None and is_int:
        try:
            return int(value)
        except ValueError:
            logger.error(f"❌ Переменная окружения {var_name} должна быть целым числом, получено: {value}")
            raise ValueError(f"Переменная {var_name} должна быть числом.")
    # logger.info(f"⚙️ {var_name}: {'*****' if 'KEY' in var_name or 'TOKEN' in var_name else value}") # Логируем для отладки (осторожно с секретами)
    return value

# --- Основные настройки ---
BOT_TOKEN = get_env_var("BOT_TOKEN", required=True)
CHANNEL_ID = get_env_var("CHANNEL_ID", required=True, is_int=True)
ADMIN_ID = get_env_var("ADMIN_ID", required=True, is_int=True)

# --- Настройки OpenAI ---
OPENAI_API_KEY = get_env_var("OPENAI_API_KEY", required=True)
MODEL = get_env_var("MODEL", default="gpt-4o-mini")
OPENAI_PROXY = get_env_var("OPENAI_PROXY") # Может быть None

# --- Настройки Perplexity ---
PPLX_API_KEY = get_env_var("PPLX_API_KEY") # Может быть None

# --- Внутренние настройки ---
LOG_FILE_REL = get_env_var("LOG_FILE", default="../data/telegram_channel_log.csv")
PLOT_FILE_REL = get_env_var("PLOT_FILE", default="../data/posting_time_stats.png")
DEFAULT_POST_TIME = get_env_var("DEFAULT_POST_TIME", default="10:00")
DAILY_AUTO_POST_JOB = get_env_var("DAILY_AUTO_POST_JOB", default="daily_auto_post_job")
NEWS_RSS_URL = get_env_var("NEWS_RSS_URL", default="https://news.google.com/rss/search?q=artificial+intelligence&hl=ru&gl=RU&ceid=RU:ru")

# --- Абсолютные пути (важно для работы внутри Docker и с монтируемыми томами) ---
DATA_DIR = APP_DIR / "../data"
LOG_FILE = (APP_DIR / LOG_FILE_REL).resolve()
PLOT_FILE = (APP_DIR / PLOT_FILE_REL).resolve()

# Создаем директорию data, если ее нет
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Проверка, что обязательные ID не нулевые (дополнительная защита)
if not CHANNEL_ID:
    raise ValueError("CHANNEL_ID не может быть 0. Укажите корректный ID канала.")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID не может быть 0. Укажите корректный ID администратора.")

logger.info(f"Путь к лог-файлу: {LOG_FILE}")
logger.info(f"Путь к файлу графика: {PLOT_FILE}")
