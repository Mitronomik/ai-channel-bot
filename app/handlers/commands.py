# -*- coding: utf-8 -*-
import logging
import httpx        # Используем для RSS и Perplexity
import feedparser   # Для парсинга RSS
import ssl          # Для обработки SSL ошибок
from datetime import datetime, time as dtime, timezone
import pandas as pd

from telegram import Update, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden, BadRequest # Добавили BadRequest

# Импорт локальных модулей
from .. import config
from ..openai_client import get_async_openai_client # Используем async клиент
from ..post_logger import read_top_posts, read_posts, log_post
from ..prompts import PROMPT_TMPL_IDEA, PROMPT_TMPL_NEWS, PROMPT_TMPL_RESEARCH
from ..utils import get_best_posting_time
from .callbacks import INLINE_ACTION_KB # Импортируем клавиатуру для черновиков
from .jobs import auto_post_job # Импортируем функцию для автопостинга

logger = logging.getLogger(__name__)

# --- Reply клавиатура (основное меню) ---
MENU_KB = ReplyKeyboardMarkup(
    [
        ["💡 Идея", "📰 Новости"],
        ["📊 Статистика", "🕒 Авто по лучшему"],
        ["📅 Отчёт за неделю", "🔍 Ресёрч PPLX"],
        ["⚙️ Расписание", "🛑 Остановить автопост"],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
    is_persistent=True
)

# --- Команда /start ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветствие и клавиатуру админу."""
    if not update.message or not update.effective_user: return
    user_id = update.effective_user.id
    if user_id != config.ADMIN_ID:
        logger.warning(f"Неавторизованный доступ к /start от user_id: {user_id}")
        return
    try:
        await update.message.reply_text(
            "🤖 Привет! Я твой AI ассистент для канала.\nВыбери действие:",
            reply_markup=MENU_KB
        )
    except (TelegramError, Forbidden) as e:
        logger.error(f"Ошибка отправки /start сообщения админу {user_id}: {e}")

# --- Команда /idea (и для кнопки "💡 Идея") ---
async def generate_idea(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Генерирует черновик идеи для поста с помощью OpenAI."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    try:
        await update.message.reply_chat_action(action='typing')
    except TelegramError as e:
        logger.warning(f"Не удалось отправить chat_action 'typing': {e}")

    try:
        # 1. Получаем лучшие посты из лога
        logger.debug("Запрос топ постов для генерации идеи...")
        top_posts_df = read_top_posts(5)
        if not top_posts_df.empty:
            posts_context = top_posts_df[['text', 'reactions']].to_string(index=False, header=True)
            logger.debug(f"Топ посты найдены. Контекст ({len(posts_context)} симв.):\n{posts_context[:500]}...")
        else:
            posts_context = "(Пока нет данных о прошлых постах)"
            logger.debug("Данные о прошлых постах отсутствуют.")

        # 2. Формируем промпт
        prompt = PROMPT_TMPL_IDEA.format(posts=posts_context)
        logger.debug(f"Сформирован промпт для OpenAI ({len(prompt)} симв.).")

        # 3. Вызываем OpenAI API (асинхронно)
        openai_client = get_async_openai_client()
        if not openai_client:
             logger.error("Не удалось получить клиент OpenAI для generate_idea.")
             await ctx.bot.send_message(config.ADMIN_ID, "❌ Ошибка: Не удалось инициализировать клиент OpenAI.")
             return

        draft = None
        last_err = None
        used_model = config.MODEL

        async def try_generate(model_name):
            nonlocal draft, last_err, used_model
            try:
                 logger.info(f"Запрос к OpenAI (модель: {model_name}) для генерации идеи...")
                 resp = await openai_client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1200, # Увеличим лимит для более длинных постов
                    temperature=0.7, # Можно чуть уменьшить для большей предсказуемости
                 )
                 if resp.choices and resp.choices[0].message and resp.choices[0].message.content:
                     draft = resp.choices[0].message.content.strip()
                     used_model = model_name
                     logger.info(f"Идея успешно сгенерирована моделью {model_name}.")
                     return True
                 else:
                     logger.warning(f"Ответ от OpenAI модели {model_name} не содержит текста.")
                     last_err = "Ответ API не содержит текста."
                     return False
            except Exception as e:
                 last_err = e
                 logger.warning(f"Модель {model_name} не сработала: {type(e).__name__}: {e}")
                 # Добавим лог для PermissionDeniedError
                 if isinstance(e, Forbidden) or (hasattr(e, 'code') and e.code == 'unsupported_country_region_territory'):
                     logger.error("-> Ошибка доступа к OpenAI API (403 Forbidden / unsupported_country). Проверьте прокси или регион аккаунта.")
                 return False

        logger.info(f"Попытка генерации идеи с основной моделью: {config.MODEL}")
        success = await try_generate(config.MODEL)

        if not success and config.MODEL != "gpt-3.5-turbo":
             logger.warning(f"Основная модель {config.MODEL} не сработала, пробую gpt-3.5-turbo...")
             success = await try_generate("gpt-3.5-turbo")

        # 4. Отправляем результат админу
        if success and draft:
            notice = "💡 Черновик:"
            if used_model != config.MODEL:
                 notice = f"⚠️ Использована резервная модель {used_model}.\n{notice}"
            await ctx.bot.send_message(config.ADMIN_ID, f"{notice}\n{draft}", reply_markup=INLINE_ACTION_KB)
        elif draft is None and success:
            error_message = "❌ Ошибка: OpenAI вернул пустой результат."
            logger.error(error_message)
            await ctx.bot.send_message(config.ADMIN_ID, error_message)
        else:
            error_text = f"❌ Ошибка OpenAI при генерации идеи: {type(last_err).__name__}"
            # Добавляем специфичное сообщение для ошибки доступа
            if isinstance(last_err, Forbidden) or (hasattr(last_err, 'code') and last_err.code == 'unsupported_country_region_territory'):
                 error_text += "\n(Вероятно, проблема с доступом из вашего региона или прокси. Проверьте настройки.)"
            else:
                 error_text += f": {last_err}" # Добавляем детали для других ошибок

            logger.error(f"Ошибка OpenAI при генерации идеи. Последняя ошибка: {last_err}", exc_info=isinstance(last_err, Exception))
            await ctx.bot.send_message(config.ADMIN_ID, error_text)

    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка в generate_idea: {e}", exc_info=True)
        try:
            # Упрощаем сообщение об ошибке
            await ctx.bot.send_message(config.ADMIN_ID, f"❌ Внутренняя ошибка при генерации идеи: {type(e).__name__}")
        except Exception as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке generate_idea админу: {send_e}")


# --- Команда /news (и для кнопки "📰 Новости") (Используем HTTX) ---
async def generate_news_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Генерирует черновик поста на основе новостей из RSS, используя httpx."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    try:
        await update.message.reply_chat_action(action='typing')
    except TelegramError as e:
        logger.warning(f"Не удалось отправить chat_action 'typing': {e}")

    rss_url = config.NEWS_RSS_URL
    if not rss_url:
         logger.error("URL RSS ленты новостей не указан в конфигурации (NEWS_RSS_URL).")
         await ctx.bot.send_message(config.ADMIN_ID, "❌ URL RSS ленты не настроен.")
         return

    # --- 1. Загрузка RSS ленты с использованием httpx ---
    logger.info(f"Загрузка новостей из RSS (httpx): {rss_url}")
    feed_data = {}
    try:
        # Используем httpx.AsyncClient для асинхронного запроса
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, verify=True) as client:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            response = await client.get(rss_url, headers=headers)

            logger.debug(f"Ответ от RSS сервера: Статус {response.status_code}")
            response.raise_for_status() # Проверка на ошибки HTTP (4xx, 5xx)

            rss_content = response.content
            if not rss_content:
                 logger.error(f"Получен пустой ответ от RSS URL: {rss_url}")
                 await ctx.bot.send_message(config.ADMIN_ID, "❌ Получен пустой ответ от RSS-сервера.")
                 return

            logger.debug(f"Попытка парсинга RSS контента ({len(rss_content)} байт)...")
            feed_data = feedparser.parse(rss_content)
            logger.info(f"RSS лента загружена и передана в feedparser.")

    except httpx.HTTPStatusError as e:
        logger.error(f"Ошибка HTTP {e.response.status_code} при загрузке RSS {rss_url}", exc_info=False)
        await ctx.bot.send_message(config.ADMIN_ID, f"❌ Ошибка HTTP {e.response.status_code} при загрузке новостей.")
        return
    except httpx.TimeoutException as e:
         logger.error(f"Таймаут при загрузке RSS {rss_url}: {e}", exc_info=False)
         await ctx.bot.send_message(config.ADMIN_ID, "❌ Таймаут при загрузке новостей.")
         return
    except httpx.RequestError as e:
        # Особое внимание на SSL ошибки
        if isinstance(e, httpx.ConnectError) and e.__cause__ and isinstance(e.__cause__, ssl.SSLError):
             ssl_error_details = repr(e.__cause__)
             logger.error(f"Ошибка SSL при подключении к RSS {rss_url}: {ssl_error_details}", exc_info=False)
             await ctx.bot.send_message(config.ADMIN_ID, f"❌ Ошибка SSL при загрузке новостей: {type(e.__cause__).__name__}")
        else:
             logger.error(f"Ошибка сети/запроса при загрузке RSS {rss_url}: {e}", exc_info=True)
             await ctx.bot.send_message(config.ADMIN_ID, f"❌ Ошибка сети при загрузке новостей: {type(e).__name__}")
        return
    except Exception as e: # Ловим другие ошибки (например, feedparser)
        logger.error(f"Ошибка при обработке RSS {rss_url}: {e}", exc_info=True)
        await ctx.bot.send_message(config.ADMIN_ID, f"❌ Ошибка обработки RSS ленты: {type(e).__name__}")
        return
    # --- Конец блока загрузки RSS ---

    # Проверка результата парсинга
    if not feed_data or feed_data.get('bozo', 1) or not feed_data.entries:
        bozo_exception = feed_data.get('bozo_exception', 'Неизвестная ошибка парсинга')
        logger.warning(f"Не удалось распарсить RSS ({rss_url}) или лента пуста. Bozo: {feed_data.get('bozo', 'N/A')}, Exception: {bozo_exception}")
        error_msg_detail = type(bozo_exception).__name__ if bozo_exception != 'Неизвестная ошибка парсинга' else bozo_exception
        await ctx.bot.send_message(config.ADMIN_ID, f"❌ Не удалось разобрать новости из RSS: {error_msg_detail}")
        return

    # --- Блок 2: Форматирование новостей ---
    news_items_context = ""
    from bs4 import BeautifulSoup
    for entry in feed_data.entries[:7]:
        title = entry.get('title', 'Без заголовка')
        summary = entry.get('summary', '')
        summary_text = BeautifulSoup(summary, "html.parser").get_text(separator=' ', strip=True)
        news_items_context += f"- {title}: {summary_text[:150]}...\n"

    if not news_items_context:
         logger.warning("Не удалось извлечь тексты новостей из записей RSS.")
         await ctx.bot.send_message(config.ADMIN_ID, "❌ Не удалось извлечь тексты новостей из RSS.")
         return
    logger.debug(f"Контекст новостей для генерации поста:\n{news_items_context}")

    # --- Блок 3: Формирование промпта ---
    prompt = PROMPT_TMPL_NEWS.format(news_items=news_items_context)

    # --- Блок 4: Вызов OpenAI ---
    openai_client = get_async_openai_client()
    if not openai_client:
         logger.error("Не удалось получить клиент OpenAI для generate_news_post.")
         await ctx.bot.send_message(config.ADMIN_ID, "❌ Ошибка: Не удалось инициализировать клиент OpenAI.")
         return

    draft = None
    last_err = None
    used_model = config.MODEL

    async def try_generate_news(model_name):
        nonlocal draft, last_err, used_model
        try:
            logger.info(f"Запрос к OpenAI (модель: {model_name}) для генерации новости...")
            resp = await openai_client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500, # Увеличим для развернутых постов
                temperature=0.65,
            )
            if resp.choices and resp.choices[0].message and resp.choices[0].message.content:
                 draft = resp.choices[0].message.content.strip()
                 used_model = model_name
                 logger.info(f"Новостной пост успешно сгенерирован моделью {model_name}.")
                 return True
            else:
                 logger.warning(f"Ответ от OpenAI модели {model_name} (новости) не содержит текста.")
                 last_err = "Ответ API не содержит текста."
                 return False
        except Exception as e:
            last_err = e
            logger.warning(f"Модель {model_name} не сработала (новости): {type(e).__name__}: {e}")
            if isinstance(e, Forbidden) or (hasattr(e, 'code') and e.code == 'unsupported_country_region_territory'):
                logger.error("-> Ошибка доступа к OpenAI API (403 Forbidden / unsupported_country). Проверьте прокси или регион аккаунта.")
            return False

    success = await try_generate_news(config.MODEL)
    if not success and config.MODEL != "gpt-3.5-turbo":
        logger.warning(f"Основная модель {config.MODEL} не сработала для новости, пробую gpt-3.5-turbo...")
        success = await try_generate_news("gpt-3.5-turbo")

    # --- Блок 5: Отправка результата ---
    if success and draft:
        notice = "📰 Новость:"
        if used_model != config.MODEL:
             notice = f"⚠️ Использована резервная модель {used_model}.\n{notice}"
        await ctx.bot.send_message(config.ADMIN_ID, f"{notice}\n{draft}", reply_markup=INLINE_ACTION_KB)
    elif draft is None and success:
         error_message = "❌ Ошибка: OpenAI вернул пустой результат для новости."
         logger.error(error_message)
         await ctx.bot.send_message(config.ADMIN_ID, error_message)
    else:
        error_text = f"❌ Ошибка OpenAI при генерации новости: {type(last_err).__name__}"
        if isinstance(last_err, Forbidden) or (hasattr(last_err, 'code') and last_err.code == 'unsupported_country_region_territory'):
             error_text += "\n(Проблема с доступом из вашего региона/прокси.)"
        else:
             error_text += f": {last_err}"

        logger.error(f"Ошибка OpenAI при генерации новости. Последняя ошибка: {last_err}", exc_info=isinstance(last_err, Exception))
        await ctx.bot.send_message(config.ADMIN_ID, error_text)

    # Перехват исключений на уровне всей функции (на всякий случай)
    # except Exception as e:
    #     logger.error(f"❌ Непредвиденная ошибка в generate_news_post: {e}", exc_info=True)
    #     try:
    #         await ctx.bot.send_message(config.ADMIN_ID, f"❌ Внутренняя ошибка при обработке новостей: {type(e).__name__}")
    #     except Exception as send_e:
    #          logger.error(f"Не удалось отправить сообщение об ошибке generate_news_post админу: {send_e}")


# --- Команда /stats (и для кнопки "📊 Статистика") ---
async def show_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отправляет статистику по лучшему времени и график."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    try:
        await update.message.reply_chat_action(action='upload_photo')
    except TelegramError as e:
        logger.warning(f"Не удалось отправить chat_action 'upload_photo': {e}")

    try:
        logger.info("Запрос статистики лучшего времени постинга...")
        best_time, plot_path = get_best_posting_time() # Используем исправленную функцию
        message = f"📊 **Анализ времени публикаций**\n\n"
        message += f"🕒 Рекомендуемое время для постинга (UTC): **{best_time}**\n\n" # Уточнили UTC
        message += f"📈 График среднего числа реакций по часам (UTC):"

        await ctx.bot.send_message(config.ADMIN_ID, message, parse_mode=ParseMode.MARKDOWN)

        if plot_path and plot_path.exists():
            logger.info(f"Отправка графика статистики: {plot_path}")
            try:
                 with open(plot_path, "rb") as photo_file:
                     await ctx.bot.send_photo(config.ADMIN_ID, photo=photo_file)
                 logger.info(f"График статистики {plot_path} успешно отправлен админу.")
            except FileNotFoundError:
                 logger.error(f"Файл графика {plot_path} не найден для отправки.")
                 await ctx.bot.send_message(config.ADMIN_ID, "⚠️ Ошибка: Файл графика не найден.")
            except (TelegramError, Forbidden) as e:
                 logger.error(f"Не удалось отправить график {plot_path} админу: {e}")
                 await ctx.bot.send_message(config.ADMIN_ID, f"⚠️ Не удалось отправить файл графика: {type(e).__name__}")
        elif plot_generated := plot_path: # Если путь был, но файла нет
            logger.warning(f"Файл графика {plot_path} не существует.")
            await ctx.bot.send_message(config.ADMIN_ID, "⚠️ Не удалось найти сгенерированный график.")
        else: # Если plot_path изначально None
             logger.info("График не сгенерирован (нет данных или ошибка).")
             await ctx.bot.send_message(config.ADMIN_ID, "📉 График не сгенерирован (вероятно, недостаточно данных для анализа).")

    except Exception as e:
        logger.error(f"❌ Ошибка в show_stats: {e}", exc_info=True)
        try:
            await ctx.bot.send_message(config.ADMIN_ID, f"❌ Ошибка при показе статистики: {type(e).__name__}")
        except Exception as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке show_stats админу: {send_e}")


# --- Команда /auto_best (и для кнопки "🕒 Авто по лучшему") ---
async def set_auto_post_best_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Настраивает ежедневный автопостинг на лучшее время."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    if not ctx.job_queue:
        logger.error("JobQueue не доступен в контексте для set_auto_post_best_time.")
        await update.message.reply_text("❌ Ошибка: Планировщик задач недоступен.")
        return

    try:
        logger.info("Запрос лучшего времени для настройки автопостинга...")
        best_time_str, _ = get_best_posting_time()
        try:
            hour = int(best_time_str.split(":")[0])
        except (ValueError, IndexError, TypeError) as time_e:
            logger.error(f"Не удалось разобрать лучшее время '{best_time_str}': {time_e}. Используется дефолтное.")
            hour = int(config.DEFAULT_POST_TIME.split(":")[0])
            best_time_str = f"{hour:02d}:00"

        post_time = dtime(hour=hour, minute=0, second=0, tzinfo=timezone.utc)
        logger.info(f"Определено время для автопостинга: {post_time.strftime('%H:%M')} UTC")

        current_jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
        removed_count = 0
        for job in current_jobs:
            job.schedule_removal()
            removed_count += 1
        if removed_count > 0:
            logger.info(f"Удалено {removed_count} предыдущих задач '{config.DAILY_AUTO_POST_JOB}'.")

        ctx.job_queue.run_daily(
            callback=auto_post_job,
            time=post_time,
            name=config.DAILY_AUTO_POST_JOB,
            data={"channel_id": config.CHANNEL_ID, "admin_id": config.ADMIN_ID}
        )

        logger.info(f"Задача '{config.DAILY_AUTO_POST_JOB}' запланирована на {post_time.strftime('%H:%M')} UTC.")
        await update.message.reply_text(f"✅ Автопостинг настроен на **{best_time_str} UTC** ежедневно.", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"❌ Ошибка при настройке автопостинга: {e}", exc_info=True)
        try:
            await update.message.reply_text(f"❌ Не удалось настроить автопостинг: {type(e).__name__}")
        except Exception as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке set_auto_post_best_time админу: {send_e}")


# --- Команда /weekly_report (и для кнопки "📅 Отчёт за неделю") ---
async def weekly_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Генерирует и отправляет отчет по постам за последнюю неделю."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    try:
        await update.message.reply_chat_action(action='typing')
    except TelegramError as e:
        logger.warning(f"Не удалось отправить chat_action 'typing': {e}")

    try:
        logger.info("Генерация недельного отчета...")
        df = read_posts()
        if df.empty or 'dt' not in df.columns or df['dt'].isnull().all():
            logger.warning("Нет данных для недельного отчета.")
            await update.message.reply_text("❌ Нет данных для отчёта.")
            return

        if df['dt'].dt.tz is None:
            logger.warning("Таймзоны в логе нет. Предполагается UTC.")
            df['dt'] = df['dt'].dt.tz_localize(timezone.utc)

        now = datetime.now(timezone.utc)
        one_week_ago = now - pd.Timedelta(days=7)
        weekly_df = df[df['dt'] > one_week_ago].copy()

        if weekly_df.empty:
            logger.info("Нет постов за последнюю неделю.")
            await update.message.reply_text("📉 За последнюю неделю нет новых постов в логе.")
            return

        total_posts = len(weekly_df)
        average_reactions = weekly_df['reactions'].fillna(0).mean()
        total_reactions = weekly_df['reactions'].fillna(0).sum()
        top_posts = weekly_df.nlargest(3, 'reactions')

        report = f"📅 **Отчёт за последнюю неделю** ({one_week_ago.strftime('%d.%m.%Y')} - {now.strftime('%d.%m.%Y')})\n\n"
        report += f"📝 Всего постов: {total_posts}\n"
        report += f"📈 Сумма реакций: {int(total_reactions)}\n"
        report += f"📊 Среднее число реакций: {average_reactions:.1f}\n\n"

        if not top_posts.empty:
            report += "🏆 **Топ-3 поста по реакциям:**\n"
            for index, row in top_posts.iterrows():
                 text_preview = row['text'].replace('\n', ' ').strip()[:70]
                 report += f"  🔥 {int(row['reactions'])} реакций - _{text_preview}..._\n"
        else:
            report += "ℹ️ Недостаточно данных для определения топ постов за неделю.\n"

        await ctx.bot.send_message(config.ADMIN_ID, report, parse_mode=ParseMode.MARKDOWN)
        logger.info("Недельный отчет успешно отправлен админу.")

    except Exception as e:
        logger.error(f"❌ Ошибка при генерации недельного отчета: {e}", exc_info=True)
        try:
            await update.message.reply_text(f"❌ Ошибка при формировании отчета: {type(e).__name__}")
        except Exception as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке weekly_report админу: {send_e}")


# --- Команда /research (и для кнопки "🔍 Ресёрч PPLX") (ИСПРАВЛЕНАЯ МОДЕЛЬ И ОБРАБОТКА ОШИБОК) ---
async def research_perplexity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выполняет поиск и генерацию поста через Perplexity API."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    if not config.PPLX_API_KEY:
        logger.warning("Попытка использовать Perplexity без API ключа.")
        await ctx.bot.send_message(config.ADMIN_ID, "❗️ API-ключ Perplexity (PPLX_API_KEY) не найден.")
        return

    query = " ".join(ctx.args) if ctx.args else "последние тренды в области искусственного интеллекта"
    logger.info(f"Запрос к Perplexity API по теме: '{query}'")
    try:
        await update.message.reply_text(f"🔬 Ищу информацию по запросу: '{query}'...")
        await update.message.reply_chat_action(action='typing')
    except TelegramError as e:
         logger.warning(f"Не удалось отправить сообщение/chat_action в research_perplexity: {e}")

    headers = {
        "Authorization": f"Bearer {config.PPLX_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "model": "sonar", # Исправленная/актуальная модель
        "messages": [
            {"role": "system", "content": "You are an AI assistant writing concise and engaging Telegram posts for an IT audience."}, # Уточнили роль
            {"role": "user", "content": PROMPT_TMPL_RESEARCH.format(query=query)}
        ],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            logger.debug(f"Ответ от Perplexity API: Статус {res.status_code}")
            if res.status_code == 401:
                 logger.error("Ошибка 401 Unauthorized от Perplexity API. Проверьте PPLX_API_KEY.")
                 await ctx.bot.send_message(config.ADMIN_ID, "❌ Ошибка авторизации (401) Perplexity. Проверьте ключ.")
                 return

            res.raise_for_status() # Проверка на другие ошибки (включая 400 Bad Request из-за неверной модели)
            data = res.json()
            logger.debug(f"Получены данные от Perplexity: {str(data)[:500]}...")

        if isinstance(data, dict) and "choices" in data and data["choices"] and \
           isinstance(data["choices"][0], dict) and "message" in data["choices"][0] and \
           isinstance(data["choices"][0]["message"], dict) and "content" in data["choices"][0]["message"]:

            text = data["choices"][0]["message"]["content"].strip()
            if text:
                logger.info(f"Perplexity успешно сгенерировал ответ по запросу: {query}")
                await ctx.bot.send_message(config.ADMIN_ID, f"💡 Черновик (Perplexity):\n{text}", reply_markup=INLINE_ACTION_KB)
            else:
                logger.warning("Perplexity API вернул пустой 'content'.")
                await ctx.bot.send_message(config.ADMIN_ID, "❌ Perplexity API вернул пустой ответ.")
        else:
            error_detail = data.get('error', {}).get('message', 'Неверный формат ответа')
            logger.error(f"Ошибка API Perplexity или неверный формат ответа: {error_detail} | Ответ: {data}")
            await ctx.bot.send_message(config.ADMIN_ID, f"❌ Ошибка API Perplexity: {error_detail}")

    except httpx.HTTPStatusError as e:
         error_body = e.response.text[:200] if hasattr(e.response, 'text') else '(нет тела ответа)'
         logger.error(f"❌ Ошибка HTTP {e.response.status_code} от Perplexity: {error_body}", exc_info=False)
         # Упрощенное сообщение для пользователя
         await ctx.bot.send_message(config.ADMIN_ID, f"❌ Ошибка HTTP {e.response.status_code} от Perplexity API.")
    except httpx.RequestError as e:
         logger.error(f"❌ Ошибка сети при запросе к Perplexity: {e}", exc_info=True)
         await ctx.bot.send_message(config.ADMIN_ID, f"❌ Ошибка сети при обращении к Perplexity: {type(e).__name__}")
    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка в research_perplexity: {e}", exc_info=True)
        try:
            await ctx.bot.send_message(config.ADMIN_ID, f"❌ Внутренняя ошибка при ресёрче: {type(e).__name__}")
        except Exception as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке research_perplexity админу: {send_e}")


# --- Команда /schedule (и для кнопки "⚙️ Расписание") ---
async def show_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показывает текущее расписание автопостинга (если есть)."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    schedule_text = "⚙️ **Статус автопостинга:**\n\n"
    if ctx.job_queue:
        jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
        if jobs:
            job = jobs[0]
            trigger = job.trigger
            next_run_time = job.next_t

            run_hour = getattr(trigger, 'hour', None)
            run_minute = getattr(trigger, 'minute', 0)

            if run_hour is not None:
                try:
                    scheduled_time_str = f"{int(run_hour):02d}:{int(run_minute):02d}"
                    schedule_text += f"✅ Автопостинг **включен**.\n"
                    schedule_text += f"🕒 Публикация настроена на **{scheduled_time_str} UTC** ежедневно.\n\n"
                except (ValueError, TypeError):
                    logger.error(f"Не удалось отформатировать время из триггера: hour={run_hour}, minute={run_minute}")
                    schedule_text += f"✅ Автопостинг **включен**, но не удалось определить время настройки.\n"
                if next_run_time:
                     schedule_text += f"▶️ Следующий запуск: {next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
                else:
                     schedule_text += f"▶️ Время следующего запуска пока не определено.\n\n"
            else:
                schedule_text += f"✅ Автопостинг **включен**, но не удалось определить время настройки из триггера.\n"
                if next_run_time:
                    schedule_text += f"🕒 Следующий запуск: {next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
                else:
                    schedule_text += f"🕒 Время следующего запуска пока не определено.\n\n"
            schedule_text += f"Нажмите [🛑 Остановить автопост], чтобы выключить."
        else:
            schedule_text += f"❌ Автопостинг **выключен**.\n\n"
            schedule_text += f"Нажмите [🕒 Авто по лучшему], чтобы включить."
    else:
        schedule_text += "⚠️ Планировщик задач недоступен."

    try:
        await update.message.reply_text(schedule_text, parse_mode=ParseMode.MARKDOWN)
    except (TelegramError, Forbidden) as e:
         logger.error(f"Не удалось отправить сообщение /schedule админу: {e}")


# --- Команда /stop_auto (и для кнопки "🛑 Остановить автопост") ---
async def stop_auto_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Останавливает запланированный автопостинг."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    if not ctx.job_queue:
        logger.error("JobQueue не доступен для stop_auto_post.")
        await update.message.reply_text("❌ Ошибка: Планировщик задач недоступен.")
        return

    jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
    if jobs:
        removed_count = 0
        for job in jobs:
            job.schedule_removal()
            removed_count += 1
        logger.info(f"Удалено {removed_count} задач '{config.DAILY_AUTO_POST_JOB}' по команде админа.")
        await update.message.reply_text("🛑 Ежедневный автопостинг остановлен.")
    else:
        logger.info("Задачи автопостинга для остановки не найдены.")
        await update.message.reply_text("ℹ️ Автопостинг не был запущен.")


# --- Сборка хэндлеров команд ---
start_handler = CommandHandler("start", start)
idea_handler = CommandHandler("idea", generate_idea)
news_handler = CommandHandler("news", generate_news_post)
stats_handler = CommandHandler("stats", show_stats)
auto_best_handler = CommandHandler("auto_best", set_auto_post_best_time)
weekly_report_handler = CommandHandler("weekly", weekly_report)
research_handler = CommandHandler("research", research_perplexity)
schedule_handler = CommandHandler("schedule", show_schedule)
stop_auto_handler = CommandHandler("stop_auto", stop_auto_post)

# Список всех хэндлеров команд для удобного добавления в bot.py
command_handlers = [
    start_handler, idea_handler, news_handler, stats_handler,
    auto_best_handler, weekly_report_handler, research_handler,
    schedule_handler, stop_auto_handler
]
