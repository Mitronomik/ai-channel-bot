import logging
import httpx
from openai import OpenAI, AsyncOpenAI
from . import config

logger = logging.getLogger(__name__)

_sync_client = None
_async_client = None

def get_openai_client() -> OpenAI:
    """Возвращает синхронный клиент OpenAI с настройками прокси."""
    global _sync_client
    if _sync_client is None:
        try:
            http_client_args = {}
            if config.OPENAI_PROXY:
                logger.info(f"Используется OpenAI прокси: {config.OPENAI_PROXY}")
                # Настройка транспорта для httpx с прокси
                transport = httpx.HTTPTransport(proxy=config.OPENAI_PROXY)
                # Для SOCKS прокси может потребоваться AsyncHTTPTransport и асинхронный клиент
                # или использование httpx[socks] и передача socks-proxy URL
                http_client_args['transport'] = transport
            else:
                logger.info("Прокси для OpenAI не используется.")

            _sync_client = OpenAI(
                api_key=config.OPENAI_API_KEY,
                http_client=httpx.Client(**http_client_args) if http_client_args else None
            )
            # Пробный запрос для проверки соединения (опционально, может стоить денег)
            # _sync_client.models.list()
            logger.info("Синхронный клиент OpenAI успешно инициализирован.")
        except Exception as e:
            logger.error(f"❌ Не удалось инициализировать синхронный клиент OpenAI: {e}", exc_info=True)
            raise
    return _sync_client

def get_async_openai_client() -> AsyncOpenAI:
    """Возвращает асинхронный клиент OpenAI с настройками прокси."""
    global _async_client
    if _async_client is None:
        try:
            http_client_args = {}
            if config.OPENAI_PROXY:
                logger.info(f"Используется OpenAI прокси для асинхронного клиента: {config.OPENAI_PROXY}")
                # Для асинхронного клиента нужен AsyncHTTPTransport
                proxies = {"all://": config.OPENAI_PROXY} # Более универсальный способ для httpx > 0.20
                async_transport = httpx.AsyncHTTPTransport(proxies=proxies)
                # Используем httpx.AsyncClient с транспортом
                http_client_args['http_client'] = httpx.AsyncClient(transport=async_transport)
            else:
                logger.info("Прокси для OpenAI (async) не используется.")

            _async_client = AsyncOpenAI(
                api_key=config.OPENAI_API_KEY,
                **http_client_args # Передаем http_client если он создан
            )
            # Пробный асинхронный запрос (опционально)
            # await _async_client.models.list()
            logger.info("Асинхронный клиент OpenAI успешно инициализирован.")
        except Exception as e:
            logger.error(f"❌ Не удалось инициализировать асинхронный клиент OpenAI: {e}", exc_info=True)
            raise
    return _async_client

# Пример использования (необязательно здесь, будет в handlers)
# async def test_async_client():
#     client = get_async_openai_client()
#     try:
#         completion = await client.chat.completions.create(...)
#         print(completion)
#     except Exception as e:
#         print(f"Error testing async client: {e}")
