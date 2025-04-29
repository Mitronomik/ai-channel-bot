import logging
from telegram.ext import ContextTypes
from telegram.error import TelegramError
# Импортируем необходимые функции из других модулей
from .. import config
from ..openai_client import get_async_openai_client
from ..post_logger import read_top_posts, log_post
from ..prompts import PROMPT_TMPL_AUTO # Используем авто-промпт (сейчас = idea)

logger = logging.getLogger(__name__)

async def auto_post_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Функция, выполняемая планировщиком для автоматической публикации поста.
    Генерирует идею и публикует её в канал.
    """
    job = context.job
    logger.info(f"🚀 Запуск задачи автопостинга: {job.name}")

    try:
        # 1. Получаем данные из job.data (если передавали)
        # channel_id = job.data.get("channel_id", config.CHANNEL_ID)
        # Можно использовать config.CHANNEL_ID напрямую, если он не меняется
        channel_id = config.CHANNEL_ID

        # 2. Генерируем контент (аналогично /idea)
        top_posts_df = read_top_posts(5)
        posts_context = top_posts_df[['text', 'reactions']].to_string(index=False) if not top_posts_df.empty else "(Нет данных о прошлых постах)"
        prompt = PROMPT_TMPL_AUTO.format(posts=posts_context)

        openai_client = get_async_openai_client()
        draft = None
        last_err = None
        used_model = config.MODEL

        # Попытка генерации (только основная модель для автопоста, чтобы не усложнять)
        try:
            logger.info(f"Автопост: Запрос к OpenAI (модель: {config.MODEL})...")
            resp = await openai_client.chat.completions.create(
                model=config.MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.75,
            )
            draft = resp.choices[0].message.content.strip()
            logger.info(f"Автопост: Идея успешно сгенерирована моделью {config.MODEL}.")
        except Exception as e:
            last_err = e
            logger.error(f"❌ Автопост: Ошибка генерации OpenAI ({config.MODEL}): {e}")
            # Можно попробовать резервную модель или просто пропустить этот запуск
            # Пока пропустим, чтобы не спамить ошибками
            await context.bot.send_message(
                 chat_id=config.ADMIN_ID, # Уведомляем админа об ошибке
                 text=f"⚠️ Автопост: Не удалось сгенерировать контент.\nОшибка: {e}"
            )
            return # Прерываем выполнение задачи

        # 3. Публикуем сгенерированный пост в канал
        if draft:
            try:
                sent_message = await context.bot.send_message(
                    chat_id=channel_id,
                    text=draft
                )
                logger.info(f"✅ Автопост успешно опубликован в канал {channel_id}, message_id={sent_message.message_id}")

                # 4. Логируем опубликованный пост
                try:
                    log_post(
                        message_id=sent_message.message_id,
                        text=draft,
                        timestamp=sent_message.date
                    )
                except Exception as log_e:
                    logger.error(f"❌ Автопост: Ошибка логирования поста {sent_message.message_id}: {log_e}")
                    # Отправляем уведомление админу о проблеме с логированием
                    await context.bot.send_message(
                         chat_id=config.ADMIN_ID,
                         text=f"⚠️ Автопост опубликован (ID: {sent_message.message_id}), но произошла ошибка при его логировании: {log_e}"
                    )

            except TelegramError as e:
                logger.error(f"❌ Автопост: Не удалось опубликовать пост в канал {channel_id}: {e}", exc_info=True)
                await context.bot.send_message(
                    chat_id=config.ADMIN_ID,
                    text=f"❌ Автопост: Не удалось опубликовать сгенерированный пост в канал {channel_id}.\nОшибка: {e}"
                )
            except Exception as e:
                 logger.error(f"❌ Автопост: Непредвиденная ошибка при публикации: {e}", exc_info=True)
                 await context.bot.send_message(
                    chat_id=config.ADMIN_ID,
                    text=f"❌ Автопост: Непредвиденная ошибка при публикации: {e}"
                )
        else:
            # Эта ветка не должна достигаться из-за return выше, но на всякий случай
            logger.error("❌ Автопост: Сгенерирован пустой черновик, публикация отменена.")
            await context.bot.send_message(
                chat_id=config.ADMIN_ID,
                text="⚠️ Автопост: OpenAI вернул пустой результат, публикация отменена."
            )

    except Exception as e:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА в задаче автопостинга {job.name}: {e}", exc_info=True)
        # Отправляем уведомление админу о критической ошибке в самой задаче
        try:
            await context.bot.send_message(
                chat_id=config.ADMIN_ID,
                text=f"🚨 КРИТИЧЕСКАЯ ОШИБКА в задаче автопостинга! Задача могла быть прервана.\nОшибка: {e}"
            )
        except Exception as send_e:
            logger.error(f"Не удалось даже отправить уведомление об ошибке админу: {send_e}")
