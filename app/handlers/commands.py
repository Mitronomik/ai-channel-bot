# -*- coding: utf-8 -*-
import logging
import httpx
import requests # Для Perplexity
import feedparser # Для новостей
from datetime import datetime, time as dtime, timezone # Добавлен timezone
import pandas as pd

from telegram import Update, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden # Добавлена Forbidden

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
    if not update.message or not update.effective_user: return # Доп. проверка
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
        openai_client = get_async_openai_client() # Получаем клиент (может вызвать ошибку, если не инициализирован)
        if not openai_client: # Добавим проверку на случай ошибки инициализации
             logger.error("Не удалось получить клиент OpenAI для generate_idea.")
             await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❌ Ошибка: Не удалось инициализировать клиент OpenAI.")
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
                    max_tokens=400,
                    temperature=0.75,
                 )
                 # Проверяем ответ
                 if resp.choices and resp.choices[0].message and resp.choices[0].message.content:
                     draft = resp.choices[0].message.content.strip()
                     used_model = model_name
                     logger.info(f"Идея успешно сгенерирована моделью {model_name}.")
                     return True
                 else:
                     logger.warning(f"Ответ от OpenAI модели {model_name} не содержит ожидаемого текста.")
                     last_err = "Ответ API не содержит текста."
                     return False
            except Exception as e:
                 last_err = e
                 logger.warning(f"Модель {model_name} не сработала: {type(e).__name__}: {e}")
                 return False

        # Пытаемся с основной моделью
        logger.info(f"Попытка генерации идеи с основной моделью: {config.MODEL}")
        success = await try_generate(config.MODEL)

        # Если основная не сработала, пытаемся с резервной
        if not success and config.MODEL != "gpt-3.5-turbo":
             logger.warning(f"Основная модель {config.MODEL} не сработала, пробую gpt-3.5-turbo...")
             success = await try_generate("gpt-3.5-turbo")

        # 4. Отправляем результат админу
        if success and draft:
            notice = "💡 Черновик:"
            if used_model != config.MODEL:
                 notice = f"⚠️ Использована резервная модель {used_model}.\n{notice}"
            await ctx.bot.send_message(
                 chat_id=config.ADMIN_ID,
                 text=f"{notice}\n{draft}",
                 reply_markup=INLINE_ACTION_KB
            )
        elif draft is None and success: # Если вернулся пустой draft
            error_message = "❌ Ошибка: OpenAI вернул пустой результат."
            logger.error(error_message)
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=error_message)
        else: # Если была ошибка
            error_message = f"❌ Ошибка OpenAI при генерации идеи.\nПоследняя ошибка: {type(last_err).__name__}: {last_err}"
            logger.error(error_message, exc_info=isinstance(last_err, Exception)) # Показываем traceback для исключений
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=error_message)

    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка в generate_idea: {e}", exc_info=True)
        try:
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Произошла внутренняя ошибка при генерации идеи: {e}")
        except (TelegramError, Forbidden) as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке generate_idea админу: {send_e}")


# --- Команда /news (и для кнопки "📰 Новости") ---
async def generate_news_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Генерирует черновик поста на основе новостей из RSS."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    try:
        await update.message.reply_chat_action(action='typing')
    except TelegramError as e:
        logger.warning(f"Не удалось отправить chat_action 'typing': {e}")

    rss_url = config.NEWS_RSS_URL
    if not rss_url:
         logger.error("URL RSS ленты новостей не указан в конфигурации (NEWS_RSS_URL).")
         await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❌ URL RSS ленты не настроен.")
         return

    try:
        # 1. Загружаем RSS ленту
        logger.info(f"Загрузка новостей из RSS: {rss_url}")
        # Добавляем user-agent, чтобы выглядеть как браузер
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}
        feed_data = {}
        try:
            # Используем requests для загрузки с таймаутом и user-agent
            response = requests.get(rss_url, headers=headers, timeout=45)
            response.raise_for_status() # Проверка на HTTP ошибки
            # feedparser может работать напрямую с текстом
            feed_data = feedparser.parse(response.content)
            logger.info(f"RSS лента загружена, статус: {feed_data.get('status', 'N/A')}, количество записей: {len(feed_data.entries)}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка сети при загрузке RSS {rss_url}: {e}")
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Ошибка сети при загрузке новостей: {e}")
            return
        except Exception as e: # Ловим другие возможные ошибки feedparser
            logger.error(f"Ошибка парсинга RSS {rss_url}: {e}", exc_info=True)
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Ошибка обработки RSS ленты: {e}")
            return


        if not feed_data or feed_data.get('bozo', 1) or not feed_data.entries:
            # bozo=1 означает ошибку парсинга
            bozo_exception = feed_data.get('bozo_exception', 'Неизвестная ошибка парсинга')
            logger.warning(f"Не удалось получить/распарсить новости из RSS ({rss_url}) или лента пуста. Bozo: {feed_data.get('bozo', 'N/A')}, Exception: {bozo_exception}")
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Не удалось загрузить или разобрать новости из RSS: {bozo_exception}")
            return

        # 2. Форматируем новости для промпта
        news_items_context = ""
        from bs4 import BeautifulSoup # Импортируем здесь, т.к. используется только тут
        for entry in feed_data.entries[:7]: # Берем первые 7
            title = entry.get('title', 'Без заголовка')
            summary = entry.get('summary', '')
            # Очистка HTML из summary
            summary_text = BeautifulSoup(summary, "html.parser").get_text(separator=' ', strip=True)
            news_items_context += f"- {title}: {summary_text[:150]}...\n"

        if not news_items_context:
             logger.warning("Не удалось извлечь тексты новостей из записей RSS.")
             await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❌ Не удалось извлечь тексты новостей из RSS.")
             return

        logger.debug(f"Контекст новостей для генерации поста:\n{news_items_context}")

        # 3. Формируем промпт
        prompt = PROMPT_TMPL_NEWS.format(news_items=news_items_context)

        # 4. Вызываем OpenAI API (асинхронно)
        openai_client = get_async_openai_client()
        if not openai_client:
             logger.error("Не удалось получить клиент OpenAI для generate_news_post.")
             await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❌ Ошибка: Не удалось инициализировать клиент OpenAI.")
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
                    max_tokens=500,
                    temperature=0.6,
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
                return False

        success = await try_generate_news(config.MODEL)
        if not success and config.MODEL != "gpt-3.5-turbo":
            logger.warning(f"Основная модель {config.MODEL} не сработала для новости, пробую gpt-3.5-turbo...")
            success = await try_generate_news("gpt-3.5-turbo")

        # 5. Отправляем результат админу
        if success and draft:
            notice = "📰 Новость:"
            if used_model != config.MODEL:
                 notice = f"⚠️ Использована резервная модель {used_model}.\n{notice}"
            await ctx.bot.send_message(
                 chat_id=config.ADMIN_ID,
                 text=f"{notice}\n{draft}",
                 reply_markup=INLINE_ACTION_KB
            )
        elif draft is None and success:
             error_message = "❌ Ошибка: OpenAI вернул пустой результат для новости."
             logger.error(error_message)
             await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=error_message)
        else:
            error_message = f"❌ Ошибка OpenAI при генерации новости.\nПоследняя ошибка: {type(last_err).__name__}: {last_err}"
            logger.error(error_message, exc_info=isinstance(last_err, Exception))
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=error_message)

    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка в generate_news_post: {e}", exc_info=True)
        try:
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Внутренняя ошибка при обработке новостей: {e}")
        except (TelegramError, Forbidden) as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке generate_news_post админу: {send_e}")


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
        best_time, plot_path = get_best_posting_time()
        message = f"📊 **Анализ времени публикаций**\n\n" # Используем Markdown
        message += f"🕒 Рекомендуемое время для постинга (по среднему числу реакций): **{best_time}**\n\n"
        message += f"📈 График среднего числа реакций по часам:"

        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=message, parse_mode=ParseMode.MARKDOWN)

        if plot_path and plot_path.exists():
            logger.info(f"Отправка графика статистики: {plot_path}")
            try:
                 # Используем with open для гарантии закрытия файла
                 with open(plot_path, "rb") as photo_file:
                     await ctx.bot.send_photo(chat_id=config.ADMIN_ID, photo=photo_file)
                 logger.info(f"График статистики {plot_path} успешно отправлен админу.")
            except FileNotFoundError:
                 logger.error(f"Файл графика {plot_path} не найден для отправки (хотя exists() вернул True?).")
                 await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="⚠️ Ошибка: Файл графика не найден.")
            except (TelegramError, Forbidden) as e:
                 logger.error(f"Не удалось отправить график {plot_path} админу: {e}")
                 await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"⚠️ Не удалось отправить файл графика: {e}")
        elif plot_path:
            logger.warning(f"Файл графика {plot_path} не существует (plot_path был возвращен, но exists() == False).")
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="⚠️ Не удалось найти сгенерированный график.")
        else:
             logger.info("График не сгенерирован (недостаточно данных или ошибка генерации).")
             await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="📉 График не сгенерирован (вероятно, недостаточно данных для анализа).")

    except Exception as e:
        logger.error(f"❌ Ошибка в show_stats: {e}", exc_info=True)
        try:
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Произошла ошибка при показе статистики: {e}")
        except (TelegramError, Forbidden) as send_e:
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
        best_time_str, _ = get_best_posting_time() # Игнорируем путь к графику
        # Обработка возможной ошибки парсинга времени
        try:
            hour = int(best_time_str.split(":")[0])
        except (ValueError, IndexError) as time_e:
            logger.error(f"Не удалось разобрать лучшее время '{best_time_str}': {time_e}. Используется время по умолчанию.")
            hour = int(config.DEFAULT_POST_TIME.split(":")[0]) # Берем час из дефолтного времени
            best_time_str = f"{hour:02d}:00" # Обновляем строку для сообщения пользователю

        post_time = dtime(hour=hour, minute=0, second=0, tzinfo=timezone.utc) # Указываем UTC явно, PTB работает с UTC
        logger.info(f"Определено время для автопостинга: {post_time.strftime('%H:%M')} UTC")

        # Удаляем старые задачи с ТЕМ ЖЕ именем, если они есть
        current_jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
        removed_count = 0
        for job in current_jobs:
            job.schedule_removal()
            removed_count += 1
        if removed_count > 0:
            logger.info(f"Удалено {removed_count} предыдущих задач автопостинга с именем '{config.DAILY_AUTO_POST_JOB}'.")

        # Планируем новую задачу
        ctx.job_queue.run_daily(
            callback=auto_post_job, # Функция, которая будет выполняться
            time=post_time,         # Время UTC
            name=config.DAILY_AUTO_POST_JOB,
            # chat_id и user_id больше не нужны в run_daily в PTB v20, используем data
            data={"channel_id": config.CHANNEL_ID, "admin_id": config.ADMIN_ID} # Передаем нужные ID
        )

        logger.info(f"Задача автопостинга '{config.DAILY_AUTO_POST_JOB}' успешно запланирована на {post_time.strftime('%H:%M')} UTC ежедневно.")
        await update.message.reply_text(f"✅ Автопостинг настроен на ежедневную публикацию в **{best_time_str} UTC** (на основе анализа).", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"❌ Ошибка при настройке автопостинга: {e}", exc_info=True)
        try:
            await update.message.reply_text(f"❌ Не удалось настроить автопостинг: {e}")
        except (TelegramError, Forbidden) as send_e:
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
            logger.warning("Нет данных для недельного отчета (лог пуст или нет дат).")
            await update.message.reply_text("❌ Нет данных для отчёта (лог пуст или не содержит дат).")
            return

        # Убедимся, что dt имеет таймзону (иначе now() будет offset-naive/aware mismatch)
        if df['dt'].dt.tz is None:
            logger.warning("Временные метки в логе не содержат информации о часовом поясе. Предполагается UTC.")
            df['dt'] = df['dt'].dt.tz_localize(timezone.utc)

        # Фильтруем посты за последние 7 дней
        now = datetime.now(timezone.utc) # Работаем в UTC
        one_week_ago = now - pd.Timedelta(days=7)
        weekly_df = df[df['dt'] > one_week_ago].copy()

        if weekly_df.empty:
            logger.info("За последнюю неделю нет новых постов в логе.")
            await update.message.reply_text("📉 За последнюю неделю нет новых постов в логе.")
            return

        # Анализ
        total_posts = len(weekly_df)
        average_reactions = weekly_df['reactions'].fillna(0).mean()
        total_reactions = weekly_df['reactions'].fillna(0).sum()
        top_posts = weekly_df.nlargest(3, 'reactions') # Более эффективный способ найти топ N

        # Формируем отчет
        report = f"📅 **Отчёт за последнюю неделю** ({one_week_ago.strftime('%d.%m.%Y')} - {now.strftime('%d.%m.%Y')})\n\n"
        report += f"📝 Всего постов: {total_posts}\n"
        report += f"📈 Сумма реакций: {int(total_reactions)}\n" # Убрали .0f
        report += f"📊 Среднее число реакций: {average_reactions:.1f}\n\n"

        if not top_posts.empty:
            report += "🏆 **Топ-3 поста по реакциям:**\n"
            for index, row in top_posts.iterrows():
                 text_preview = row['text'].replace('\n', ' ').strip()[:70]
                 report += f"  🔥 {int(row['reactions'])} реакций - _{text_preview}..._\n" # Убрали .0f
        else:
            report += "ℹ️ Недостаточно данных для определения топ постов за неделю.\n"

        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=report, parse_mode=ParseMode.MARKDOWN)
        logger.info("Недельный отчет успешно отправлен админу.")

    except Exception as e:
        logger.error(f"❌ Ошибка при генерации недельного отчета: {e}", exc_info=True)
        try:
            await update.message.reply_text(f"❌ Произошла ошибка при формировании отчета: {e}")
        except (TelegramError, Forbidden) as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке weekly_report админу: {send_e}")


# --- Команда /research (и для кнопки "🔍 Ресёрч PPLX") ---
async def research_perplexity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выполняет поиск и генерацию поста через Perplexity API."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    if not config.PPLX_API_KEY:
        logger.warning("Попытка использовать Perplexity без API ключа.")
        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❗️ API-ключ Perplexity (PPLX_API_KEY) не найден в настройках (.env). Эта функция недоступна.")
        return

    query = " ".join(ctx.args) if ctx.args else "последние тренды в области искусственного интеллекта"
    logger.info(f"Запрос к Perplexity API по теме: '{query}'")
    try:
        await update.message.reply_text(f"🔬 Ищу информацию по запросу: '{query}' через Perplexity...")
        await update.message.reply_chat_action(action='typing')
    except TelegramError as e:
         logger.warning(f"Не удалось отправить сообщение/chat_action в research_perplexity: {e}")


    headers = {
        "Authorization": f"Bearer {config.PPLX_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # Модель Perplexity - убедитесь, что она актуальна
    # Список моделей: https://docs.perplexity.ai/docs/model-cards
    # sonar-small-32k-online / sonar-large-32k-online
    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": "You are an AI assistant writing concise and engaging Telegram posts."},
            {"role": "user", "content": PROMPT_TMPL_RESEARCH.format(query=query)}
        ],
        "stream": False,
        # Можно добавить temperature, max_tokens и т.д. по желанию
    }

    try:
        # Используем httpx для асинхронного запроса
        async with httpx.AsyncClient(timeout=60.0) as client: # Увеличен таймаут
            res = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            # Логируем статус ответа
            logger.debug(f"Ответ от Perplexity API: Статус {res.status_code}")
            # Подробная проверка на 401
            if res.status_code == 401:
                 logger.error("Ошибка 401 Unauthorized от Perplexity API. Проверьте PPLX_API_KEY.")
                 await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❌ Ошибка авторизации (401) с Perplexity API. Проверьте ваш PPLX_API_KEY в .env.")
                 return # Выходим, чтобы не вызывать raise_for_status()

            res.raise_for_status() # Проверка на другие HTTP ошибки (4xx, 5xx)
            data = res.json()
            logger.debug(f"Получены данные от Perplexity: {str(data)[:500]}...") # Логируем начало ответа

        # Проверяем структуру ответа
        if isinstance(data, dict) and "choices" in data and data["choices"] and \
           isinstance(data["choices"][0], dict) and "message" in data["choices"][0] and \
           isinstance(data["choices"][0]["message"], dict) and "content" in data["choices"][0]["message"]:

            text = data["choices"][0]["message"]["content"].strip()
            if text:
                logger.info(f"Perplexity успешно сгенерировал ответ по запросу: {query}")
                await ctx.bot.send_message(
                    chat_id=config.ADMIN_ID,
                    text=f"💡 Черновик (Perplexity):\n{text}",
                    reply_markup=INLINE_ACTION_KB
                )
            else:
                logger.warning("Perplexity API вернул пустой 'content' в ответе.")
                await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❌ Ошибка: Perplexity API вернул пустой ответ.")
        else:
            error_detail = data.get('error', {}).get('message', 'Ответ не содержит данных или имеет неверную структуру')
            logger.error(f"Ошибка от API Perplexity или неверный формат ответа: {error_detail} | Ответ: {data}")
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Ошибка API Perplexity: {error_detail}")

    except httpx.HTTPStatusError as e:
         # Обрабатываем ошибки, не пойманные ранее (например, 5xx)
         error_body = e.response.text[:200] if hasattr(e.response, 'text') else '(нет тела ответа)'
         logger.error(f"❌ Ошибка HTTP {e.response.status_code} при запросе к Perplexity API: {error_body}", exc_info=True)
         await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Ошибка HTTP {e.response.status_code} от Perplexity API.\n{error_body}")
    except httpx.RequestError as e:
         # Ошибки сети/соединения
         logger.error(f"❌ Ошибка сети при запросе к Perplexity API: {e}", exc_info=True)
         await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Ошибка сети при обращении к Perplexity: {e}")
    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка в research_perplexity: {e}", exc_info=True)
        try:
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Внутренняя ошибка при ресёрче: {e}")
        except (TelegramError, Forbidden) as send_e:
             logger.error(f"Не удалось отправить сообщение об ошибке research_perplexity админу: {send_e}")


# --- Команда /schedule (и для кнопки "⚙️ Расписание") (ИСПРАВЛЕННАЯ ВЕРСИЯ) ---
async def show_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показывает текущее расписание автопостинга (если есть)."""
    if not update.message or not update.effective_user: return
    if update.effective_user.id != config.ADMIN_ID: return

    schedule_text = "⚙️ **Статус автопостинга:**\n\n"
    if ctx.job_queue:
        jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
        if jobs:
            # Есть запланированная задача
            job = jobs[0] # Берем первую (должна быть одна)
            trigger = job.trigger
            next_run_time = job.next_t # Время следующего запуска (может быть None)

            # Пытаемся получить время из триггера
            run_hour = getattr(trigger, 'hour', None)
            run_minute = getattr(trigger, 'minute', 0) # Дефолт 0, если нет

            if run_hour is not None:
                # Если удалось получить время из триггера
                # Форматируем время, учитывая возможные int/str
                try:
                    scheduled_time_str = f"{int(run_hour):02d}:{int(run_minute):02d}"
                    schedule_text += f"✅ Автопостинг **включен**.\n"
                    schedule_text += f"🕒 Публикация настроена на **{scheduled_time_str} UTC** ежедневно.\n\n"
                except (ValueError, TypeError):
                    logger.error(f"Не удалось отформатировать время из триггера: hour={run_hour}, minute={run_minute}")
                    schedule_text += f"✅ Автопостинг **включен**, но не удалось определить время настройки.\n"

                # Дополнительно покажем время следующего запуска, если оно есть
                if next_run_time:
                     # Форматируем с указанием таймзоны (UTC по умолчанию для PTB)
                     schedule_text += f"▶️ Следующий запуск: {next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
                else:
                     schedule_text += f"▶️ Время следующего запуска пока не определено (возможно, задача только что добавлена).\n\n"

            else:
                # Если не удалось получить час из триггера (маловероятно для run_daily)
                schedule_text += f"✅ Автопостинг **включен**, но не удалось определить точное время настройки из триггера.\n"
                if next_run_time:
                    schedule_text += f"🕒 Следующий запуск: {next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
                else:
                    schedule_text += f"🕒 Время следующего запуска пока не определено.\n\n"

            # Общая часть для включенного автопостинга
            schedule_text += f"Нажмите [🛑 Остановить автопост], чтобы выключить."

        else:
            # Если задач с таким именем нет
            schedule_text += f"❌ Автопостинг **выключен**.\n\n"
            schedule_text += f"Нажмите [🕒 Авто по лучшему], чтобы проанализировать статистику и включить автопостинг на рекомендуемое время."
    else:
        # Если JobQueue недоступен
        schedule_text += "⚠️ Планировщик задач недоступен, статус неизвестен."

    # Отправляем сообщение
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
        logger.error("JobQueue не доступен в контексте для stop_auto_post.")
        await update.message.reply_text("❌ Ошибка: Планировщик задач недоступен.")
        return

    jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
    if jobs:
        removed_count = 0
        for job in jobs:
            job.schedule_removal()
            removed_count += 1
        logger.info(f"Удалено {removed_count} задач автопостинга с именем '{config.DAILY_AUTO_POST_JOB}' по команде пользователя.")
        await update.message.reply_text("🛑 Ежедневный автопостинг остановлен.")
    else:
        logger.info("Задачи автопостинга для остановки не найдены.")
        await update.message.reply_text("ℹ️ Автопостинг не был запущен (нет активных задач).")


# --- Сборка хэндлеров команд ---
start_handler = CommandHandler("start", start)
idea_handler = CommandHandler("idea", generate_idea)
news_handler = CommandHandler("news", generate_news_post)
stats_handler = CommandHandler("stats", show_stats)
auto_best_handler = CommandHandler("auto_best", set_auto_post_best_time)
weekly_report_handler = CommandHandler("weekly", weekly_report) # /weekly для краткости
research_handler = CommandHandler("research", research_perplexity)
schedule_handler = CommandHandler("schedule", show_schedule)
stop_auto_handler = CommandHandler("stop_auto", stop_auto_post)

# Список всех хэндлеров команд для удобного добавления в bot.py
command_handlers = [
    start_handler, idea_handler, news_handler, stats_handler,
    auto_best_handler, weekly_report_handler, research_handler,
    schedule_handler, stop_auto_handler
]
