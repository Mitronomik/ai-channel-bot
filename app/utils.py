# -*- coding: utf-8 -*-
import logging
import pandas as pd # Убедимся, что pandas импортирован
import matplotlib
matplotlib.use('Agg') # Устанавливаем бэкенд для работы без GUI (ДО импорта pyplot)
import matplotlib.pyplot as plt
from pathlib import Path
import httpx # Для асинхронных запросов скачивания
import io    # Для работы с байтами изображения в памяти

# Импортируем локальные модули
from . import config
from .post_logger import read_posts # Импортируем функцию чтения логов

logger = logging.getLogger(__name__)

# Определяем путь к файлу графика из конфигурации
PLOT_PATH = config.PLOT_FILE

# --- Функция анализа лучшего времени постинга (ИСПРАВЛЕННАЯ ВЕРСИЯ 3) ---
def get_best_posting_time() -> tuple[str, Path | None]:
    """
    Анализирует лог постов и определяет лучшее время для публикации.
    Сохраняет график статистики по часам, если возможно.
    Возвращает кортеж: (строка с лучшим временем 'ЧЧ:00', путь к файлу графика | None).
    """
    logger.debug("Начало анализа лучшего времени постинга.")
    df = read_posts()

    # Проверка на наличие данных
    if df.empty or 'dt' not in df.columns or 'reactions' not in df.columns or df['dt'].isnull().all():
        logger.warning("Нет данных для анализа времени. Возвращаем дефолт.")
        return config.DEFAULT_POST_TIME, None

    df = df.dropna(subset=['dt'])
    if df.empty:
        logger.warning("Нет валидных дат после очистки. Возвращаем дефолт.")
        return config.DEFAULT_POST_TIME, None

    try:
        df['hour'] = df['dt'].dt.hour
    except AttributeError as e:
         logger.error(f"Ошибка извлечения часа: {e}", exc_info=True)
         return config.DEFAULT_POST_TIME, None

    # Считаем среднее по часам
    best_time_str = config.DEFAULT_POST_TIME
    plot_generated = None
    hourly_stats = pd.Series(dtype=float)

    try:
        hourly_stats = df.groupby('hour')['reactions'].apply(lambda x: x.fillna(0).mean())
        logger.debug(f"Статистика по часам:\n{hourly_stats}")

        # Ищем лучший час ТОЛЬКО если hourly_stats - непустая Series
        if isinstance(hourly_stats, pd.Series) and not hourly_stats.empty:
            best_hour_index = hourly_stats.idxmax()
            best_time_str = f"{int(best_hour_index):02d}:00"
            logger.info(f"Рекомендуемое время (из Series): {best_time_str}")
        # Если hourly_stats - число (данные за 1 час), то лучший час - этот час
        elif isinstance(hourly_stats, (int, float)) and not pd.isna(hourly_stats):
             unique_hours = df['hour'].unique()
             if len(unique_hours) == 1:
                 best_hour_index = unique_hours[0]
                 best_time_str = f"{int(best_hour_index):02d}:00"
                 logger.info(f"Рекомендуемое время (данные за 1 час): {best_time_str}")
             else: # Нелогичная ситуация
                 logger.warning("hourly_stats - число, но часов > 1. Используем дефолт.")
        else: # Если пустая Series или NaN
            logger.warning("Не удалось рассчитать статистику по часам. Используем дефолт.")

    except Exception as e:
        logger.error(f"Ошибка при расчете лучшего часа: {e}. Используем дефолт.", exc_info=True)

    # --- Визуализация ---
    fig = None
    try:
        # !!! Строим график ТОЛЬКО если hourly_stats - это непустая Series !!!
        if isinstance(hourly_stats, pd.Series) and not hourly_stats.empty:
            fig, ax = plt.subplots(figsize=(10, 5))
            hourly_stats.plot(kind='bar', ax=ax, title="Среднее число реакций по часам публикации") # Вызов plot только здесь
            ax.set_xlabel("Час дня (UTC)")
            ax.set_ylabel("Среднее кол-во реакций")
            plt.xticks(rotation=0)
            plt.tight_layout()
            PLOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(PLOT_PATH)
            logger.info(f"График сохранен в {PLOT_PATH}")
            plot_generated = PLOT_PATH
        else:
             logger.info("Недостаточно данных (требуется Series) для построения графика.")

    except Exception as e:
        logger.error(f"❌ Ошибка создания/сохранения графика: {e}", exc_info=True)
        plot_generated = None
    finally:
        if fig is not None: plt.close(fig)
        elif plt.get_fignums(): plt.close('all')
        logger.debug("Фигура графика закрыта.")

    return best_time_str, plot_generated

# --- Функция для скачивания изображения по URL ---
async def download_image(url: str) -> bytes | None:
    """
    Асинхронно скачивает изображение по URL и возвращает его как байты.
    Возвращает None в случае ошибки или некорректного URL.
    """
    # Проверка входного URL
    if not url or not isinstance(url, str) or not url.startswith(('http://', 'https://')):
        logger.error(f"Некорректный URL для скачивания изображения: {url}")
        return None

    logger.info(f"Попытка скачивания изображения с URL: {url}")
    try:
        # Используем httpx для асинхронного запроса
        # Увеличиваем таймаут и разрешаем редиректы
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, verify=True) as client: # verify=True по умолчанию, но можно указать явно
            # Добавляем User-Agent для маскировки под браузер
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            response = await client.get(url, headers=headers)

            # Логируем статус ответа
            logger.debug(f"Ответ от сервера изображения: Статус {response.status_code}")
            response.raise_for_status() # Проверка на ошибки HTTP (4xx, 5xx)

            # Проверяем тип контента (нестрогая проверка)
            content_type = response.headers.get('content-type', '').lower()
            if content_type and not content_type.startswith('image/'):
                 logger.warning(f"Скачанный файл по URL {url} имеет Content-Type: {content_type} (не image/*). Попытка использовать его всё равно.")

            # Получаем содержимое ответа
            image_bytes = response.content
            if not image_bytes:
                 logger.error(f"Скачанный файл с {url} пуст (0 байт).")
                 return None

            # Логируем размер скачанного файла
            size_kb = len(image_bytes) / 1024
            logger.info(f"Изображение успешно скачано с {url} ({size_kb:.1f} КБ)")
            return image_bytes

    except httpx.HTTPStatusError as e:
        # Ошибка от сервера (404, 403, 500 и т.д.)
        error_body = e.response.text[:200] if hasattr(e.response, 'text') else '(нет тела ответа)'
        logger.error(f"Ошибка HTTP {e.response.status_code} при скачивании изображения с {url}: {error_body}")
        return None
    except httpx.TimeoutException as e:
         logger.error(f"Таймаут при скачивании изображения с {url}: {e}")
         return None
    except httpx.RequestError as e:
        # Ошибка сети, DNS, SSL и т.д.
        logger.error(f"Ошибка сети/запроса при скачивании изображения с {url}: {e}")
        return None
    except Exception as e:
        # Любая другая непредвиденная ошибка
        logger.error(f"Непредвиденная ошибка при скачивании изображения с {url}: {e}", exc_info=True)
        return None

# ============================================================
# --- Конец файла app/utils.py ---
# ============================================================
