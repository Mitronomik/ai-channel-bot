import logging
import pandas as pd
from datetime import datetime
from pathlib import Path
from . import config

logger = logging.getLogger(__name__)

CSV_PATH = config.LOG_FILE
CSV_COLUMNS = ["message_id", "text", "timestamp_iso", "reactions"]

def _ensure_csv_exists():
    """Проверяет наличие CSV файла и создает его с заголовками при необходимости."""
    if not CSV_PATH.exists():
        try:
            logger.warning(f"Файл лога {CSV_PATH} не найден. Создание нового файла.")
            df = pd.DataFrame(columns=CSV_COLUMNS)
            df.to_csv(CSV_PATH, index=False, encoding='utf-8')
            logger.info(f"Создан пустой файл лога: {CSV_PATH}")
        except Exception as e:
            logger.error(f"❌ Не удалось создать файл лога {CSV_PATH}: {e}", exc_info=True)
            raise

def log_post(message_id: int, text: str, timestamp: datetime | None = None, reactions: int = 0):
    """Логирует пост в CSV файл."""
    _ensure_csv_exists()
    if timestamp is None:
        timestamp = datetime.now()
    timestamp_iso = timestamp.isoformat()

    new_data = pd.DataFrame([{
        "message_id": message_id,
        "text": text,
        "timestamp_iso": timestamp_iso,
        "reactions": reactions
    }])

    try:
        # Используем режим 'a' (append) и отключаем запись заголовка, если файл уже существует
        new_data.to_csv(CSV_PATH, mode='a', header=not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0, index=False, encoding='utf-8')
        logger.info(f"Пост message_id={message_id} успешно залогирован в {CSV_PATH}")
    except Exception as e:
        logger.error(f"❌ Ошибка записи поста message_id={message_id} в CSV: {e}", exc_info=True)

def read_posts() -> pd.DataFrame:
    """Читает все посты из CSV файла."""
    _ensure_csv_exists()
    try:
        df = pd.read_csv(CSV_PATH, encoding='utf-8')
        # Преобразуем нужные колонки в правильные типы, если они прочитались как строки
        if 'timestamp_iso' in df.columns:
             df['dt'] = pd.to_datetime(df['timestamp_iso'])
        if 'reactions' in df.columns:
            df['reactions'] = pd.to_numeric(df['reactions'], errors='coerce').fillna(0).astype(int)
        if 'message_id' in df.columns:
             df['message_id'] = pd.to_numeric(df['message_id'], errors='coerce').fillna(0).astype(int)

        logger.debug(f"Прочитано {len(df)} постов из {CSV_PATH}")
        return df
    except pd.errors.EmptyDataError:
        logger.warning(f"Файл лога {CSV_PATH} пуст.")
        return pd.DataFrame(columns=CSV_COLUMNS + ['dt']) # Возвращаем пустой DataFrame с ожидаемыми колонками
    except Exception as e:
        logger.error(f"❌ Ошибка чтения CSV файла {CSV_PATH}: {e}", exc_info=True)
        # В случае ошибки возвращаем пустой DataFrame, чтобы избежать падения других функций
        return pd.DataFrame(columns=CSV_COLUMNS + ['dt'])


def read_top_posts(n: int = 5) -> pd.DataFrame:
    """Читает CSV и возвращает N постов с наибольшим количеством реакций."""
    df = read_posts()
    if df.empty or 'reactions' not in df.columns:
        logger.warning("Нет данных о постах или реакциях для определения топ постов.")
        return pd.DataFrame(columns=df.columns) # Возвращаем пустой DataFrame со всеми колонками

    # Сортируем по реакциям (убывание) и берем топ N
    top_df = df.sort_values(by="reactions", ascending=False).head(n)
    logger.info(f"Найдено топ {len(top_df)} постов.")
    return top_df

# Функция обновления реакций (пока не используется активно, нужна логика вызова)
# def update_reactions(message_id: int, reactions: int):
#     _ensure_csv_exists()
#     try:
#         df = pd.read_csv(CSV_PATH, encoding='utf-8')
#         if message_id in df['message_id'].values:
#             df.loc[df['message_id'] == message_id, 'reactions'] = reactions
#             df.to_csv(CSV_PATH, index=False, encoding='utf-8')
#             logger.info(f"Реакции для поста message_id={message_id} обновлены на {reactions}.")
#         else:
#             logger.warning(f"Пост message_id={message_id} не найден в логе для обновления реакций.")
#     except Exception as e:
#         logger.error(f"❌ Ошибка обновления реакций для поста message_id={message_id}: {e}", exc_info=True)
