# -*- coding: utf-8 -*-
import logging
import httpx # Убедимся, что httpx импортирован
from openai import OpenAI, AsyncOpenAI, APIError # Импортируем нужные классы и ошибки
from . import config # Импортируем нашу конфигурацию

logger = logging.getLogger(__name__)

# Глобальные переменные для хранения инициализированных клиентов (кэширование)
_sync_client = None
_async_client = None

# --- Инициализация Синхронного Клиента ---
def get_openai_client() -> OpenAI | None:
    """
    Возвращает синхронный клиент OpenAI.
    Инициализирует его при первом вызове, учитывая настройки прокси.
    Возвращает None в случае критической ошибки инициализации.
    """
    global _sync_client
    if _sync_client is None:
        logger.info("Инициализация синхронного клиента OpenAI...")
        try:
            sync_http_client = None
            if config.OPENAI_PROXY:
                logger.info(f"Используется OpenAI прокси (синхронный): {config.OPENAI_PROXY}")
                proxies = {"all://": config.OPENAI_PROXY}
                # Создаем синхронный httpx клиент с прокси
                sync_http_client = httpx.Client(proxies=proxies, timeout=60.0) # Добавим таймаут
                logger.debug("httpx.Client для OpenAI (синхронный) с прокси создан.")
            else:
                logger.info("Прокси для OpenAI (синхронный) не используется.")
                # Можно создать клиент без прокси для управления таймаутами и др.
                sync_http_client = httpx.Client(timeout=60.0)
                logger.debug("httpx.Client для OpenAI (синхронный) без прокси создан.")

            _sync_client = OpenAI(
                api_key=config.OPENAI_API_KEY,
                # Передаем созданный http_client
                http_client=sync_http_client
            )
            # Опционально: Проверка соединения (может стоить денег/токенов)
            # try:
            #     _sync_client.models.list(limit=1)
            #     logger.info("Проверка соединения с OpenAI API (синхронный) прошла успешно.")
            # except Exception as ping_e:
            #     logger.warning(f"Не удалось выполнить пробный запрос к OpenAI API (синхронный): {ping_e}")

            logger.info("Синхронный клиент OpenAI успешно инициализирован.")

        except APIError as e:
             # Ошибка от самого API OpenAI (неверный ключ, нет доступа и т.д.)
             logger.critical(f"❌ Критическая ошибка API OpenAI при инициализации синхронного клиента: {e.status_code} - {e.message}", exc_info=False)
             # В этом случае клиент не создан, дальнейшие вызовы будут падать
             _sync_client = None # Убедимся, что остается None
             # Можно либо пробросить исключение дальше 'raise', либо вернуть None
             # raise # --> Прервет запуск бота
             return None # --> Позволит боту запуститься, но команды не будут работать
        except Exception as e:
            # Другие ошибки (например, httpx не смог подключиться к прокси)
            logger.critical(f"❌ Критическая ошибка при инициализации синхронного клиента OpenAI: {e}", exc_info=True)
            _sync_client = None
            # raise
            return None
    return _sync_client


# --- Инициализация Асинхронного Клиента (ИСПРАВЛЕННАЯ ВЕРСИЯ) ---
def get_async_openai_client() -> AsyncOpenAI | None:
    """
    Возвращает асинхронный клиент OpenAI.
    Инициализирует его при первом вызове, учитывая настройки прокси.
    Возвращает None в случае критической ошибки инициализации.
    """
    global _async_client
    if _async_client is None:
        logger.info("Инициализация асинхронного клиента OpenAI...")
        try:
            # Создаем http_client для AsyncOpenAI отдельно
            async_http_client = None
            if config.OPENAI_PROXY:
                logger.info(f"Используется OpenAI прокси (асинхронный): {config.OPENAI_PROXY}")
                proxies = {"all://": config.OPENAI_PROXY}
                # Создаем асинхронный httpx клиент с прокси
                async_http_client = httpx.AsyncClient(proxies=proxies, timeout=60.0) # Добавим таймаут
                logger.debug("httpx.AsyncClient для OpenAI (асинхронный) с прокси создан.")
            else:
                logger.info("Прокси для OpenAI (асинхронный) не используется.")
                # Создаем клиент без прокси
                async_http_client = httpx.AsyncClient(timeout=60.0)
                logger.debug("httpx.AsyncClient для OpenAI (асинхронный) без прокси создан.")

            # Инициализируем AsyncOpenAI, передавая наш httpx клиент
            _async_client = AsyncOpenAI(
                api_key=config.OPENAI_API_KEY,
                http_client=async_http_client
            )

            # Опционально: Проверка соединения (асинхронная)
            # Запуск проверки здесь может быть сложен, т.к. функция синхронная.
            # Лучше делать первую проверку при первом реальном вызове API.
            # Например, можно добавить в generate_idea/generate_news_post
            # блок try-except вокруг первого вызова и если ошибка - сбросить _async_client = None

            logger.info("Асинхронный клиент OpenAI успешно инициализирован.")

        except APIError as e:
             logger.critical(f"❌ Критическая ошибка API OpenAI при инициализации асинхронного клиента: {e.status_code} - {e.message}", exc_info=False)
             _async_client = None
             # raise
             return None
        except Exception as e:
            logger.critical(f"❌ Критическая ошибка при инициализации асинхронного клиента OpenAI: {e}", exc_info=True)
            _async_client = None
            # raise
            return None
    return _async_client


# --- Функция Генерации Изображения ---
async def generate_image(prompt: str) -> str | None:
    """
    Генерирует изображение с помощью OpenAI API (DALL-E или gpt-image-1) на основе промпта.
    Возвращает URL сгенерированного изображения или None в случае ошибки.
    """
    if not config.IMAGE_GENERATION_ENABLED:
        logger.info("Генерация изображений отключена в конфигурации.")
        return None

    # Проверка наличия и непустоты промпта
    if not prompt or not isinstance(prompt, str) or prompt.isspace():
        logger.error("Промпт для генерации изображения отсутствует или пуст.")
        return None

    # Получаем асинхронный клиент (он будет инициализирован при первом вызове)
    client = get_async_openai_client()
    if not client: # Если клиент не был инициализирован из-за ошибки
        logger.error("Не удалось получить асинхронный клиент OpenAI для генерации изображения.")
        return None

    # Обрезаем промпт, если он слишком длинный
    if len(prompt) > config.IMAGE_PROMPT_MAX_LENGTH:
        original_len = len(prompt)
        prompt = prompt[:config.IMAGE_PROMPT_MAX_LENGTH].strip() # Обрезаем и удаляем пробелы по краям
        logger.warning(f"Промпт для изображения ({original_len} симв.) обрезан до {len(prompt)} символов.")
        if not prompt: # Проверка после обрезки
             logger.error("Промпт стал пустым после обрезки до максимальной длины.")
             return None

    try:
        # Логируем начало запроса
        log_prompt = prompt.replace('\n', ' ')[:100] # Для лога заменяем переносы и берем начало
        logger.info(f"Запрос к OpenAI Images API (модель: {config.IMAGE_MODEL}, размер: {config.IMAGE_SIZE}) с промптом: '{log_prompt}...'")

        # Формируем базовые параметры запроса
        api_params = {
            "model": config.IMAGE_MODEL,
            "prompt": prompt,
            "n": 1,                     # Количество генерируемых изображений
            "size": config.IMAGE_SIZE,
            #"response_format": "url",   # Получаем URL (можно 'b64_json')
            # "user": "ai-channel-bot-user-XYZ" # Опционально: ID конечного пользователя для мониторинга злоупотреблений
        }

        # Добавляем параметры, специфичные для DALL-E 3
        if config.IMAGE_MODEL == 'dall-e-3':
            api_params["quality"] = config.IMAGE_QUALITY
            api_params["style"] = config.IMAGE_STYLE
            logger.debug(f"Добавлены параметры для DALL-E 3: quality={api_params['quality']}, style={api_params['style']}")
        # Для DALL-E 2 и gpt-image-1 эти параметры не добавляем
        elif config.IMAGE_MODEL == 'gpt-image-1':
             logger.debug(f"Для модели {config.IMAGE_MODEL} параметры response_format, quality, style не передаются.")

        # Выполняем запрос к API
        response = await client.images.generate(**api_params)

        # Анализируем ответ
        if response.data and len(response.data) > 0 and response.data[0].url:
            image_url = response.data[0].url
            # Дополнительная проверка, что URL не пустой и является строкой
            if image_url and isinstance(image_url, str):
                 logger.info(f"Изображение успешно сгенерировано моделью {config.IMAGE_MODEL}. URL получен.")
                 logger.debug(f"Image URL: {image_url}")
                 return image_url
            else:
                 logger.error(f"Ответ OpenAI Images API (модель: {config.IMAGE_MODEL}) вернул некорректный URL: {image_url}")
                 return None
        else:
            # Логируем, если структура ответа неожиданная
            response_text = str(response)[:500] # Логируем начало ответа для диагностики
            logger.error(f"Ответ OpenAI Images API (модель: {config.IMAGE_MODEL}) не содержит ожидаемых данных (URL). Ответ: {response_text}...")
            return None

    except APIError as e:
        # Обрабатываем ошибки, возвращаемые API OpenAI
        error_message = f"Ошибка API OpenAI при генерации изображения (модель: {config.IMAGE_MODEL}): Статус={e.status_code}, Тип={e.type}, Код={e.code}, Сообщение={e.message}"
        logger.error(error_message, exc_info=False) # Не выводим полный traceback для APIError, т.к. сообщение информативно
        # Дополнительная подсказка для пользователя, если проблема с gpt-image-1
        if config.IMAGE_MODEL == 'gpt-image-1' and e.code == 'invalid_request_error' and 'model_not_found' in str(e.message).lower():
             logger.error("-> Модель 'gpt-image-1' не найдена или недоступна для вашего аккаунта. Пожалуйста, проверьте модель в .env или используйте 'dall-e-3'.")
        elif e.code == 'billing_not_active' or e.code == 'insufficient_quota':
             logger.error("-> Проблема с биллингом OpenAI или исчерпана квота. Проверьте ваш аккаунт OpenAI.")
        # Возвращаем None, чтобы бот мог обработать отсутствие картинки
        return None
    except httpx.HTTPStatusError as e:
        # Ошибки HTTP от httpx (например, от прокси)
        logger.error(f"❌ Ошибка HTTP {e.response.status_code} при запросе к OpenAI Images API: {e.response.text[:200]}", exc_info=False)
        return None
    except httpx.RequestError as e:
        # Ошибки сети (таймаут, DNS и т.д.) при запросе к OpenAI
        logger.error(f"❌ Ошибка сети при запросе к OpenAI Images API: {e}", exc_info=True)
        return None
    except Exception as e:
        # Любые другие непредвиденные ошибки
        logger.error(f"❌ Непредвиденная ошибка при вызове OpenAI Images API (модель: {config.IMAGE_MODEL}): {e}", exc_info=True)
        return None

# ============================================================
# --- Конец файла app/openai_client.py ---
# ============================================================
