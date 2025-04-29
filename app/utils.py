import logging
import pandas as pd
import matplotlib
matplotlib.use('Agg') # Важно для работы без GUI (на сервере)
import matplotlib.pyplot as plt
from pathlib import Path
from . import config
from .post_logger import read_posts # Импортируем функцию чтения логов

logger = logging.getLogger(__name__)

PLOT_PATH = config.PLOT_FILE

def get_best_posting_time() -> tuple[str, Path | None]:
    """
    Анализирует лог постов и определяет лучшее время для публикации.
    Сохраняет график статистики.
    Возвращает строку с лучшим временем (ЧЧ:00) и путь к файлу графика (или None).
    """
    df = read_posts()

    if df.empty or 'dt' not in df.columns or 'reactions' not in df.columns or df['dt'].isnull().all():
        logger.warning("Недостаточно данных для анализа лучшего времени. Возвращаем время по умолчанию.")
        return config.DEFAULT_POST_TIME, None

    # Удаляем строки, где время не распарсилось
    df = df.dropna(subset=['dt'])
    if df.empty:
        logger.warning("Нет валидных дат в логах после очистки. Возвращаем время по умолчанию.")
        return config.DEFAULT_POST_TIME, None


    df['hour'] = df['dt'].dt.hour
    # Группируем по часам и считаем СРЕДНЕЕ число реакций
    hourly_stats = df.groupby('hour')['reactions'].mean()

    if hourly_stats.empty:
        logger.warning("Не удалось сгруппировать данные по часам. Возвращаем время по умолчанию.")
        return config.DEFAULT_POST_TIME, None

    # Находим час с максимальным средним числом реакций
    best_hour = hourly_stats.idxmax()
    best_time_str = f"{int(best_hour):02d}:00"
    logger.info(f"Анализ времени: лучшее время для постинга - {best_time_str} (на основе средних реакций)")

    # --- Визуализация ---
    try:
        plt.figure(figsize=(10, 5))
        ax = hourly_stats.plot(kind='bar', title="Среднее число реакций по часам публикации")
        ax.set_xlabel("Час дня")
        ax.set_ylabel("Среднее кол-во реакций")
        plt.xticks(rotation=0)
        plt.tight_layout()
        plt.savefig(PLOT_PATH)
        logger.info(f"График статистики сохранен в {PLOT_PATH}")
        plot_generated = PLOT_PATH
    except Exception as e:
        logger.error(f"❌ Не удалось создать или сохранить график статистики: {e}", exc_info=True)
        plot_generated = None

    return best_time_str, plot_generated
