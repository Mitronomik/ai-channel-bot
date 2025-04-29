# -*- coding: utf-8 -*-
import os
import logging
from dotenv import load_dotenv
from pathlib import Path

# Определяем базовую директорию проекта
APP_DIR = Path(__file__).resolve().parent # Директория app/
BASE_DIR = APP_DIR.parent # Корень проекта

# Загружаем переменные окружения из файла .env в корне проекта
env_path = BASE_DIR / '.env'
if env_path.exists():
    load_dotenv(env_path)
    logging.info(f"Загружены переменные окружения из: {env_path}")
else:
    logging.warning(f"Файл .env не найден по пути: {env_path}. Используются переменные окружения системы или значения по умолчанию.")


logger = logging.getLogger(__name__)

def get_env_var(var_name: str, default: str | None = None, required: bool = False, is_int: bool = False) -> str | int | None:
    """Получает переменную окружения, логирует и проверяет обязательность."""
    value = os.getenv(var_name, default)
    if required and value is None:
        err_msg = f"❌ Обязательная переменная окружения {var_name} не установлена!"
        logger.critical(err_msg) # Используем critical для действительно обязательных
        raise ValueError(err_msg)
    if value is not None and is_int:
        try:
            return int(value)
        except ValueError:
            err_msg = f"❌ Переменная окружения {var_name} ('{value}') должна быть целым числом!"
            logger.error(err_msg)
            raise ValueError(err_msg)
    # Логируем для отладки (осторожно с секретами!)
    # log_value = '*****' if 'KEY' in var_name or 'TOKEN' in var_name else value
    # logger.debug(f"Переменная окружения {var_name} = {log_value}")
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

# --- Внутренние пути и настройки ---
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
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError as e:
     logger.error(f"Не удалось создать директорию {DATA_DIR}: {e}")
     # В зависимости от критичности, можно либо выйти, либо продолжить без data
     # sys.exit(1)

# Проверка, что обязательные ID не нулевые (дополнительная защита)
if not CHANNEL_ID or CHANNEL_ID == 0:
    raise ValueError("CHANNEL_ID не может быть 0. Укажите корректный ID канала (начинается с -100...).")
if not ADMIN_ID or ADMIN_ID == 0:
    raise ValueError("ADMIN_ID не может быть 0. Укажите корректный ID администратора.")

logger.info(f"Путь к лог-файлу: {LOG_FILE}")
logger.info(f"Путь к файлу графика: {PLOT_FILE}")


# ============================================================
# --- Настройки генерации изображений (ОБНОВЛЕННЫЙ БЛОК) ---
# ============================================================
IMAGE_GENERATION_ENABLED = get_env_var("IMAGE_GENERATION_ENABLED", default="False").lower() == 'true'
# Указываем 'dall-e-3' как более безопасный дефолт, пока gpt-image-1 не общедоступна
IMAGE_MODEL = get_env_var("IMAGE_MODEL", default="dall-e-3")
IMAGE_SIZE = get_env_var("IMAGE_SIZE", default="1024x1024")
IMAGE_QUALITY = get_env_var("IMAGE_QUALITY", default="standard") # Актуально для DALL-E 3
IMAGE_STYLE = get_env_var("IMAGE_STYLE", default="vivid")       # Актуально для DALL-E 3
IMAGE_PROMPT_MAX_LENGTH = get_env_var("IMAGE_PROMPT_MAX_LENGTH", default="1000", is_int=True)

logger.info(f"Генерация изображений: {'Включена' if IMAGE_GENERATION_ENABLED else 'Выключена'}")
if IMAGE_GENERATION_ENABLED:
    logger.info(f"  -> Модель для генерации изображений: {IMAGE_MODEL}")

    # --- Валидация параметров в зависимости от модели ---
    default_size_1024 = '1024x1024'
    model_validated = False

    if IMAGE_MODEL == 'dall-e-3':
        allowed_sizes = ['1024x1024', '1792x1024', '1024x1792']
        if IMAGE_SIZE not in allowed_sizes:
            logger.warning(f"Некорректный размер '{IMAGE_SIZE}' для DALL-E 3. Допустимые: {allowed_sizes}. Используется '{default_size_1024}'.")
            IMAGE_SIZE = default_size_1024
        if IMAGE_QUALITY not in ['standard', 'hd']:
            logger.warning(f"Некорректное качество '{IMAGE_QUALITY}' для DALL-E 3 (standard/hd). Используется 'standard'.")
            IMAGE_QUALITY = 'standard'
        if IMAGE_STYLE not in ['vivid', 'natural']:
             logger.warning(f"Некорректный стиль '{IMAGE_STYLE}' для DALL-E 3 (vivid/natural). Используется 'vivid'.")
             IMAGE_STYLE = 'vivid'
        logger.info(f"  -> Параметры DALL-E 3: Размер={IMAGE_SIZE}, Качество={IMAGE_QUALITY}, Стиль={IMAGE_STYLE}")
        model_validated = True

    elif IMAGE_MODEL == 'gpt-image-1':
        # Размеры взяты из предоставленного текста (связанные с ценовыми тирами)
        # Важно: Эти размеры могут быть неточными или измениться к релизу!
        allowed_sizes = ['1024x1024', '1024x1536', '1536x1024']
        logger.warning(f"Выбрана модель 'gpt-image-1'. Убедитесь, что она доступна для вашего API ключа и аккаунта OpenAI!")
        if IMAGE_SIZE not in allowed_sizes:
            logger.warning(f"Указанный размер '{IMAGE_SIZE}' может не поддерживаться 'gpt-image-1' согласно доступной информации. Документированные размеры: {allowed_sizes}. Используется '{default_size_1024}'.")
            IMAGE_SIZE = default_size_1024
        # Параметры quality и style, скорее всего, не применимы к gpt-image-1,
        # поэтому мы их здесь не валидируем и не будем передавать в API.
        logger.info(f"  -> Параметры gpt-image-1: Размер={IMAGE_SIZE} (Качество/Стиль не используются)")
        model_validated = True

    elif IMAGE_MODEL == 'dall-e-2':
         allowed_sizes = ['1024x1024', '512x512', '256x256']
         if IMAGE_SIZE not in allowed_sizes:
             logger.warning(f"Некорректный размер '{IMAGE_SIZE}' для DALL-E 2. Допустимые: {allowed_sizes}. Используется '{default_size_1024}'.")
             IMAGE_SIZE = default_size_1024
         # У DALL-E 2 нет quality/style
         logger.info(f"  -> Параметры DALL-E 2: Размер={IMAGE_SIZE}")
         model_validated = True

    else:
        logger.error(f"Неизвестная модель генерации изображений указана в настройках: '{IMAGE_MODEL}'. Пожалуйста, используйте 'dall-e-3', 'dall-e-2' или 'gpt-image-1'.")
        logger.warning(f"Используется модель по умолчанию 'dall-e-3' из-за неизвестного значения '{IMAGE_MODEL}'.")
        IMAGE_MODEL = 'dall-e-3' # Возвращаемся к безопасному дефолту
        # Заново применяем валидацию для дефолтной модели
        IMAGE_SIZE = get_env_var("IMAGE_SIZE", default="1024x1024") # Перечитываем или используем дефолт
        IMAGE_QUALITY = get_env_var("IMAGE_QUALITY", default="standard")
        IMAGE_STYLE = get_env_var("IMAGE_STYLE", default="vivid")
        # Повторная валидация для dall-e-3
        allowed_sizes_de3 = ['1024x1024', '1792x1024', '1024x1792']
        if IMAGE_SIZE not in allowed_sizes_de3: IMAGE_SIZE = default_size_1024
        if IMAGE_QUALITY not in ['standard', 'hd']: IMAGE_QUALITY = 'standard'
        if IMAGE_STYLE not in ['vivid', 'natural']: IMAGE_STYLE = 'vivid'
        logger.info(f"  -> Параметры DALL-E 3 (установлены по умолчанию): Размер={IMAGE_SIZE}, Качество={IMAGE_QUALITY}, Стиль={IMAGE_STYLE}")

    # Убедимся, что длина промпта не отрицательная
    if IMAGE_PROMPT_MAX_LENGTH <= 0:
         logger.warning(f"IMAGE_PROMPT_MAX_LENGTH должен быть положительным числом. Установлено значение по умолчанию 1000.")
         IMAGE_PROMPT_MAX_LENGTH = 1000

# ============================================================
# --- Конец блока настроек генерации изображений ---
# ============================================================
