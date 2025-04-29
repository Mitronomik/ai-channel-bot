import logging
import httpx
import requests # Для Perplexity
import feedparser # Для новостей
from datetime import datetime, time as dtime
import pandas as pd

from telegram import Update, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode
from telegram.error import TelegramError

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
        ["📊 Статистика", "🕒 Авто по лучшему"], # Переименовал для ясности
        ["📅 Отчёт за неделю", "🔍 Ресёрч PPLX"], # Уточнил ресерч
        ["⚙️ Расписание", "🛑 Остановить автопост"], # Переименовал
        # ["🎯 Автоидеи", "📅 Цикл тем"], # Убрал, т.к. логика цикла тем не реализована
    ],
    resize_keyboard=True,
    one_time_keyboard=False, # Делаем постоянной
    is_persistent=True
)

# --- Команда /start ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветствие и клавиатуру админу."""
    if update.effective_user.id != config.ADMIN_ID:
        logger.warning(f"Неавторизованный доступ к /start от user_id: {update.effective_user.id}")
        return
    await update.message.reply_text(
        "🤖 Привет! Я твой AI ассистент для канала.\nВыбери действие:",
        reply_markup=MENU_KB
    )

# --- Команда /idea (и для кнопки "💡 Идея") ---
async def generate_idea(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Генерирует черновик идеи для поста с помощью OpenAI."""
    if update.effective_user.id != config.ADMIN_ID: return
    await update.message.reply_chat_action(action='typing') # Показываем "печатает..."

    try:
        # 1. Получаем лучшие посты из лога
        top_posts_df = read_top_posts(5)
        if not top_posts_df.empty:
            # Форматируем данные о постах для промпта (только текст и реакции)
            posts_context = top_posts_df[['text', 'reactions']].to_string(index=False, header=True)
        else:
            posts_context = "(Пока нет данных о прошлых постах)"
        logger.debug(f"Контекст для генерации идеи:\n{posts_context}")

        # 2. Формируем промпт
        prompt = PROMPT_TMPL_IDEA.format(posts=posts_context)

        # 3. Вызываем OpenAI API (асинхронно)
        openai_client = get_async_openai_client()
        draft = None
        last_err = None
        used_model = config.MODEL # Начинаем с основной модели

        async def try_generate(model_name):
            nonlocal draft, last_err, used_model
            try:
                 logger.info(f"Запрос к OpenAI (модель: {model_name}) для генерации идеи...")
                 resp = await openai_client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400, # Увеличим немного лимит
                    temperature=0.75, # Чуть больше креативности
                 )
                 draft = resp.choices[0].message.content.strip()
                 used_model = model_name
                 logger.info(f"Идея успешно сгенерирована моделью {model_name}.")
                 return True # Успех
            except Exception as e:
                 last_err = e
                 logger.warning(f"Модель {model_name} не сработала: {e}")
                 return False # Неудача

        # Пытаемся с основной моделью
        success = await try_generate(config.MODEL)

        # Если основная модель не сработала, пытаемся с gpt-3.5-turbo (если это не основная)
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
                 reply_markup=INLINE_ACTION_KB # Добавляем кнопки Опубликовать/Удалить
            )
        else:
            error_message = f"❌ Ошибка OpenAI при генерации идеи.\nПоследняя ошибка: {last_err}"
            logger.error(error_message)
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=error_message)

    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка в generate_idea: {e}", exc_info=True)
        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Произошла внутренняя ошибка: {e}")


# --- Команда /news (и для кнопки "📰 Новости") ---
async def generate_news_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Генерирует черновик поста на основе новостей из RSS."""
    if update.effective_user.id != config.ADMIN_ID: return
    await update.message.reply_chat_action(action='typing')

    try:
        # 1. Загружаем RSS ленту
        logger.info(f"Загрузка новостей из RSS: {config.NEWS_RSS_URL}")
        feed = feedparser.parse(config.NEWS_RSS_URL)

        if not feed.entries:
            logger.warning("Не удалось получить новости из RSS или лента пуста.")
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❌ Не удалось загрузить новости из RSS.")
            return

        # 2. Форматируем новости для промпта (берем первые 5-7 новостей)
        news_items_context = ""
        for entry in feed.entries[:7]:
            title = entry.title
            summary = entry.summary if 'summary' in entry else ''
            # Очистка HTML из summary (если есть)
            from bs4 import BeautifulSoup
            summary_text = BeautifulSoup(summary, "html.parser").get_text()
            news_items_context += f"- {title}: {summary_text[:150]}...\n" # Берем начало описания

        if not news_items_context:
             await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❌ Не удалось извлечь тексты новостей.")
             return

        logger.debug(f"Контекст новостей для генерации поста:\n{news_items_context}")

        # 3. Формируем промпт
        prompt = PROMPT_TMPL_NEWS.format(news_items=news_items_context)

        # 4. Вызываем OpenAI API (асинхронно)
        openai_client = get_async_openai_client()
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
                    max_tokens=500, # Лимит для поста с новостями
                    temperature=0.6, # Более сдержанно для новостей
                )
                draft = resp.choices[0].message.content.strip()
                used_model = model_name
                logger.info(f"Новостной пост успешно сгенерирован моделью {model_name}.")
                return True
            except Exception as e:
                last_err = e
                logger.warning(f"Модель {model_name} не сработала (новости): {e}")
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
        else:
            error_message = f"❌ Ошибка OpenAI при генерации новости.\nПоследняя ошибка: {last_err}"
            logger.error(error_message)
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=error_message)

    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка в generate_news_post: {e}", exc_info=True)
        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Произошла внутренняя ошибка при обработке новостей: {e}")


# --- Команда /stats (и для кнопки "📊 Статистика") ---
async def show_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отправляет статистику по лучшему времени и график."""
    if update.effective_user.id != config.ADMIN_ID: return
    await update.message.reply_chat_action(action='upload_photo') # Показываем загрузку фото

    try:
        best_time, plot_path = get_best_posting_time()
        message = f"📊 Анализ времени публикаций:\n\n"
        message += f"🕒 Рекомендуемое время для постинга (по среднему числу реакций): **{best_time}**\n\n"
        message += f"📈 График среднего числа реакций по часам:"

        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=message, parse_mode=ParseMode.MARKDOWN)

        if plot_path and plot_path.exists():
            try:
                 await ctx.bot.send_photo(chat_id=config.ADMIN_ID, photo=InputFile(plot_path))
                 logger.info(f"График статистики {plot_path} отправлен админу.")
            except TelegramError as e:
                 logger.error(f"Не удалось отправить график {plot_path}: {e}")
                 await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="⚠️ Не удалось отправить файл графика.")
            except FileNotFoundError:
                 logger.error(f"Файл графика {plot_path} не найден для отправки.")
                 await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="⚠️ Файл графика не найден.")
        elif plot_path:
            logger.warning(f"Файл графика {plot_path} не существует, хотя путь был возвращен.")
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="⚠️ Не удалось сгенерировать график (возможно, нет данных).")
        else:
             await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="📉 График не сгенерирован (вероятно, недостаточно данных для анализа).")

    except Exception as e:
        logger.error(f"❌ Ошибка в show_stats: {e}", exc_info=True)
        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Произошла ошибка при показе статистики: {e}")


# --- Команда /auto_best (и для кнопки "🕒 Авто по лучшему") ---
async def set_auto_post_best_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Настраивает ежедневный автопостинг на лучшее время."""
    if update.effective_user.id != config.ADMIN_ID: return

    if not ctx.job_queue:
        logger.error("JobQueue не доступен в контексте.")
        await update.message.reply_text("❌ Ошибка: Планировщик задач недоступен.")
        return

    try:
        best_time_str, _ = get_best_posting_time()
        hour = int(best_time_str.split(":")[0])
        post_time = dtime(hour=hour, minute=0, second=0) # Время ЧЧ:00

        # Удаляем старые задачи с ТЕМ ЖЕ именем, если они есть
        current_jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
        for job in current_jobs:
            job.schedule_removal()
            logger.info(f"Удалена предыдущая задача автопостинга: {job.name}")

        # Планируем новую задачу
        ctx.job_queue.run_daily(
            callback=auto_post_job, # Функция, которая будет выполняться
            time=post_time,
            name=config.DAILY_AUTO_POST_JOB,
            chat_id=config.ADMIN_ID, # Передаем ID админа в задачу (или канала)
            user_id=config.ADMIN_ID, # ID пользователя для job.data (если нужно)
            data={"channel_id": config.CHANNEL_ID} # Можно передать доп. данные
        )

        logger.info(f"Задача автопостинга '{config.DAILY_AUTO_POST_JOB}' запланирована на {post_time.strftime('%H:%M')} ежедневно.")
        await update.message.reply_text(f"✅ Автопостинг настроен на ежедневную публикацию в **{hour:02d}:00** (на основе анализа).", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"❌ Ошибка при настройке автопостинга: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Не удалось настроить автопостинг: {e}")


# --- Команда /weekly_report (и для кнопки "📅 Отчёт за неделю") ---
async def weekly_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Генерирует и отправляет отчет по постам за последнюю неделю."""
    if update.effective_user.id != config.ADMIN_ID: return
    await update.message.reply_chat_action(action='typing')

    try:
        df = read_posts()
        if df.empty or 'dt' not in df.columns:
            await update.message.reply_text("❌ Нет данных для отчёта (лог пуст или не содержит дат).")
            return

        # Фильтруем посты за последние 7 дней
        now = datetime.now(df['dt'].dt.tz) # Используем таймзону из данных, если есть, иначе UTC/локальную
        one_week_ago = now - pd.Timedelta(days=7)
        weekly_df = df[df['dt'] > one_week_ago].copy() # Используем .copy() для избежания SettingWithCopyWarning

        if weekly_df.empty:
            await update.message.reply_text("📉 За последнюю неделю нет новых постов в логе.")
            return

        # Анализ
        total_posts = len(weekly_df)
        # Используем fillna(0) перед расчетом среднего, на случай если реакции где-то NaN
        average_reactions = weekly_df['reactions'].fillna(0).mean()
        total_reactions = weekly_df['reactions'].fillna(0).sum()
        # Находим топ-3 поста по реакциям
        top_posts = weekly_df.sort_values("reactions", ascending=False).head(3)

        # Формируем отчет
        report = f"📅 **Отчёт за последнюю неделю** ({one_week_ago.strftime('%d.%m')} - {now.strftime('%d.%m')})\n\n"
        report += f"📝 Всего постов: {total_posts}\n"
        report += f"📈 Сумма реакций: {total_reactions:.0f}\n"
        report += f"📊 Среднее число реакций: {average_reactions:.1f}\n\n"

        if not top_posts.empty:
            report += "🏆 **Топ-3 поста по реакциям:**\n"
            for index, row in top_posts.iterrows():
                 # Берем начало текста, заменяя переносы строк пробелами для краткости
                 text_preview = row['text'].replace('\n', ' ')[:70]
                 report += f"  🔥 {row['reactions']:.0f} реакций - _{text_preview}..._\n"
        else:
            report += "ℹ️ Недостаточно данных для определения топ постов за неделю.\n"

        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=report, parse_mode=ParseMode.MARKDOWN)
        logger.info("Недельный отчет успешно отправлен админу.")

    except Exception as e:
        logger.error(f"❌ Ошибка при генерации недельного отчета: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Произошла ошибка при формировании отчета: {e}")


# --- Команда /research (и для кнопки "🔍 Ресёрч PPLX") ---
async def research_perplexity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выполняет поиск и генерацию поста через Perplexity API."""
    if update.effective_user.id != config.ADMIN_ID: return

    if not config.PPLX_API_KEY:
        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text="❗️ API-ключ Perplexity (PPLX_API_KEY) не найден в настройках (.env). Эта функция недоступна.")
        return

    query = " ".join(ctx.args) if ctx.args else "последние тренды в области искусственного интеллекта" # Запрос по умолчанию
    await update.message.reply_text(f"🔬 Ищу информацию по запросу: '{query}' через Perplexity...")
    await update.message.reply_chat_action(action='typing')

    headers = {
        "Authorization": f"Bearer {config.PPLX_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "model": "llama-3-sonar-large-32k-online", # Или другая онлайн-модель pplx, например 'pplx-7b-online'
        "messages": [
            {"role": "system", "content": "You are an AI assistant writing concise and engaging Telegram posts."},
            {"role": "user", "content": PROMPT_TMPL_RESEARCH.format(query=query)}
        ],
        "stream": False,
         # "max_tokens": 150, # Можно ограничить, но промпт уже это делает
    }

    try:
        # Используем httpx для асинхронного запроса (или requests для синхронного)
        async with httpx.AsyncClient(timeout=45.0) as client: # Увеличим таймаут
            res = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers=headers,
                json=payload,
            )
            res.raise_for_status() # Проверка на HTTP ошибки (4xx, 5xx)
            data = res.json()

        if "choices" in data and data["choices"]:
            text = data["choices"][0]["message"]["content"].strip()
            logger.info(f"Perplexity успешно сгенерировал ответ по запросу: {query}")
            await ctx.bot.send_message(
                chat_id=config.ADMIN_ID,
                text=f"💡 Черновик (Perplexity):\n{text}",
                reply_markup=INLINE_ACTION_KB
            )
        else:
            error_detail = data.get('error', {}).get('message', 'Ответ не содержит данных')
            logger.error(f"Ошибка от API Perplexity: {error_detail} | Ответ: {data}")
            await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Ошибка API Perplexity: {error_detail}")

    except httpx.HTTPStatusError as e:
         error_body = e.response.text
         logger.error(f"❌ Ошибка HTTP при запросе к Perplexity API: {e.response.status_code} - {e.request.url}\n{error_body}", exc_info=True)
         await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Ошибка HTTP {e.response.status_code} от Perplexity API.\n{error_body[:200]}")
    except httpx.RequestError as e:
         logger.error(f"❌ Ошибка сети при запросе к Perplexity API: {e}", exc_info=True)
         await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Ошибка сети при обращении к Perplexity: {e}")
    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка в research_perplexity: {e}", exc_info=True)
        await ctx.bot.send_message(chat_id=config.ADMIN_ID, text=f"❌ Произошла внутренняя ошибка при ресёрче: {e}")


# --- Команда /schedule (и для кнопки "⚙️ Расписание") ---
async def show_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показывает текущее расписание автопостинга (если есть)."""
    if update.effective_user.id != config.ADMIN_ID: return

    schedule_text = "⚙️ **Статус автопостинга:**\n\n"
    if ctx.job_queue:
        jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
        if jobs:
            # Берем время из первой найденной задачи (должна быть одна)
            next_run_time = jobs[0].next_t # Время следующего запуска в UTC
            if next_run_time:
                # Конвертируем в локальное время сервера (или Московское, если нужно)
                # local_tz = datetime.now().astimezone().tzinfo # Локальная зона сервера
                # local_run_time = next_run_time.astimezone(local_tz)
                # schedule_text += f"✅ Автопостинг **включен**.\n"
                # schedule_text += f"🕒 Следующий пост будет опубликован примерно в: **{local_run_time.strftime('%H:%M')}** (по времени сервера) ежедневно.\n\n"
                # Проще показать время настройки задачи:
                run_hour = jobs[0].job.trigger.time.hour
                schedule_text += f"✅ Автопостинг **включен**.\n"
                schedule_text += f"🕒 Публикация настроена на **{run_hour:02d}:00** ежедневно.\n\n"

            else:
                 schedule_text += f"⚠️ Автопостинг запланирован, но время следующего запуска не определено (возможно, задача только что добавлена).\n\n"
            schedule_text += f"Нажмите [🛑 Остановить автопост], чтобы выключить."

        else:
            schedule_text += f"❌ Автопостинг **выключен**.\n\n"
            schedule_text += f"Нажмите [🕒 Авто по лучшему], чтобы проанализировать статистику и включить автопостинг на рекомендуемое время."
    else:
        schedule_text += "⚠️ Планировщик задач недоступен, статус неизвестен."

    await update.message.reply_text(schedule_text, parse_mode=ParseMode.MARKDOWN)


# --- Команда /stop_auto (и для кнопки "🛑 Остановить автопост") ---
async def stop_auto_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Останавливает запланированный автопостинг."""
    if update.effective_user.id != config.ADMIN_ID: return

    if not ctx.job_queue:
        logger.error("JobQueue не доступен в контексте для остановки.")
        await update.message.reply_text("❌ Ошибка: Планировщик задач недоступен.")
        return

    jobs = ctx.job_queue.get_jobs_by_name(config.DAILY_AUTO_POST_JOB)
    if jobs:
        for job in jobs:
            job.schedule_removal()
            logger.info(f"Задача автопостинга '{job.name}' удалена по команде пользователя.")
        await update.message.reply_text("🛑 Ежедневный автопостинг остановлен.")
    else:
        await update.message.reply_text("ℹ️ Автопостинг не был запущен.")


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
