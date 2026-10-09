import os
import asyncio
import logging
import aiohttp
import hashlib
import json
from aiogram import Bot, Dispatcher
from aiogram.types import Message

# ================= НАСТРОЙКИ (Читаются из GitHub Secrets / Переменных окружения) =================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL_ID = str(os.getenv("TG_CHANNEL_ID", "")).strip()

VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = str(os.getenv("VK_GROUP_ID", "")).strip()
VK_API_VERSION = "5.199"

# Защита от флуда: минимальная задержка между постами (в секундах)
MIN_POST_INTERVAL = 60  # 1 минута между постами

# Файл для хранения истории опубликованных постов
HISTORY_FILE = "published_posts.json"
# ================================================================================================

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if not all([TG_BOT_TOKEN, TG_CHANNEL_ID, VK_TOKEN, VK_GROUP_ID]):
    logging.error("❌ Не найдены все необходимые переменные окружения! Проверьте секреты.")
    exit(1)

bot = Bot(token=TG_BOT_TOKEN)
dp = Dispatcher()

def load_published_history() -> dict:
    """Загружает историю опубликованных постов"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"published": {}, "last_post_time": 0}

def save_published_history(history: dict):
    """Сохраняет историю опубликованных постов"""
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

def get_post_hash(message: Message) -> str:
    """Создаёт уникальный хэш поста для проверки дубликатов"""
    content = f"{message.chat.id}_{message.message_id}_{message.text or message.caption or ''}"
    return hashlib.md5(content.encode()).hexdigest()

async def upload_vk_photo(session: aiohttp.ClientSession, file_path: str) -> str:
    """Загружает фото на стену ВК и возвращает строку attachment (photoX_Y)"""
    logging.info(f"📷 Начинаем загрузку фото в ВК: {os.path.basename(file_path)}")
    
    # 1. Получаем URL для загрузки
    async with session.get("https://api.vk.com/method/photos.getWallUploadServer", 
                           params={
                               "group_id": VK_GROUP_ID,
                               "access_token": VK_TOKEN,
                               "v": VK_API_VERSION
                           }) as resp:
        data = await resp.json()
        if "error" in data:
            logging.error(f"❌ VK getWallUploadServer error: {data['error']}")
            raise Exception(f"VK getWallUploadServer: {data['error']}")
        upload_url = data["response"]["upload_url"]
        logging.info(f"✅ Получен upload_url для фото")

    # 2. Загружаем сам файл
    with open(file_path, "rb") as f:
        async with session.post(upload_url, data={"photo": f}) as resp:
            upload_data = await resp.json()
            logging.info(f"✅ Файл загружен на сервер ВК")

    # 3. Сохраняем фото на сервере ВК
    async with session.get("https://api.vk.com/method/photos.saveWallPhoto", 
                           params={
                               "group_id": VK_GROUP_ID,
                               "photo": upload_data["photo"],
                               "server": upload_data["server"],
                               "hash": upload_data["hash"],
                               "access_token": VK_TOKEN,
                               "v": VK_API_VERSION
                           }) as resp:
        data = await resp.json()
        if "error" in data:
            logging.error(f"❌ VK saveWallPhoto error: {data['error']}")
            raise Exception(f"VK saveWallPhoto: {data['error']}")
        photo_obj = data["response"][0]
        attachment = f"photo{photo_obj['owner_id']}_{photo_obj['id']}"
        logging.info(f"✅ Фото сохранено: {attachment}")
        return attachment

async def upload_vk_video(session: aiohttp.ClientSession, file_path: str) -> str:
    """Загружает видео в группу ВК и возвращает строку attachment (videoX_Y)"""
    logging.info(f"🎥 Начинаем загрузку видео в ВК: {os.path.basename(file_path)}")
    
    # 1. Получаем URL для загрузки видео
    async with session.get("https://api.vk.com/method/video.save", 
                           params={
                               "group_id": VK_GROUP_ID,
                               "name": os.path.basename(file_path),
                               "access_token": VK_TOKEN,
                               "v": VK_API_VERSION
                           }) as resp:
        data = await resp.json()
        if "error" in data:
            logging.error(f"❌ VK video.save error: {data['error']}")
            raise Exception(f"VK video.save: {data['error']}")
        video_data = data["response"]
        upload_url = video_data["upload_url"]
        logging.info(f"✅ Получен upload_url для видео")

    # 2. Загружаем файл (видео может грузиться долго)
    with open(file_path, "rb") as f:
        async with session.post(upload_url, data={"file": f}) as resp:
            await resp.text()
            logging.info(f"✅ Видео загружено на сервер ВК")

    attachment = f"video{video_data['owner_id']}_{video_data['id']}"
    logging.info(f"✅ Видео сохранено: {attachment}")
    return attachment

@dp.channel_post()
async def handle_channel_post(message: Message):
    # Фильтр: реагируем ТОЛЬКО на посты из нужного канала
    if str(message.chat.id) != TG_CHANNEL_ID:
        return

    logging.info(f"📨 Получен новый пост из канала {message.chat.id}")
    
    # Проверка защиты от флуда
    history = load_published_history()
    post_hash = get_post_hash(message)
    current_time = asyncio.get_event_loop().time()
    
    # 1. Проверка дубликата
    if post_hash in history["published"]:
        logging.warning(f"⚠️ Этот пост уже был опубликован ранее! Хэш: {post_hash[:8]}...")
        return
    
    # 2. Проверка времени между постами
    time_since_last = current_time - history["last_post_time"]
    if time_since_last < MIN_POST_INTERVAL:
        wait_time = MIN_POST_INTERVAL - time_since_last
        logging.warning(f"⏳ Защита от флуда: ждём {int(wait_time)} сек перед публикацией...")
        await asyncio.sleep(wait_time)
    
    text = message.text or message.caption or ""
    attachments = []
    
    # Ссылка на оригинал (работает для публичных каналов)
    link = f"https://t.me/{message.chat.username}/{message.message_id}" if message.chat.username else ""

    async with aiohttp.ClientSession() as session:
        file_path = None
        try:
            # --- Обработка ФОТО ---
            if message.photo:
                photo = message.photo[-1]  # Наилучшее качество
                file = await bot.get_file(photo.file_id)
                file_path = f"temp_{file.file_unique_id}.jpg"
                await bot.download_file(file.file_path, file_path)
                logging.info(f"📥 Фото скачано из Telegram: {file_path}")
                attachments.append(await upload_vk_photo(session, file_path))

            # --- Обработка ВИДЕО ---
            elif message.video:
                video = message.video
                file = await bot.get_file(video.file_id)
                file_path = f"temp_{file.file_unique_id}.mp4"
                await bot.download_file(file.file_path, file_path)
                logging.info(f"📥 Видео скачано из Telegram: {file_path}")
                attachments.append(await upload_vk_video(session, file_path))

            # --- Публикация на стене ВК ---
            params = {
                "owner_id": f"-{VK_GROUP_ID}",
                "message": text,
                "attachments": ",".join(attachments) if attachments else "",
                "from_group": 1,
                "access_token": VK_TOKEN,
                "v": VK_API_VERSION
            }
            if link:
                params["copyright"] = link

            logging.info(f"📤 Публикуем пост в ВК (текст: {len(text)} символов, вложений: {len(attachments)})")
            
            async with session.get("https://api.vk.com/method/wall.post", params=params) as resp:
                result = await resp.json()
                if "error" in result:
                    logging.error(f"❌ Ошибка VK API wall.post: {result['error']}")
                    # Если всё же флуд, добавляем задержку и пробуем ещё раз через минуту
                    if result['error'].get('error_code') == 9:
                        logging.warning("⏳ Флуд-контроль сработал. Ждём 60 секунд и повторяем...")
                        await asyncio.sleep(60)
                        # Повторная попытка
                        async with session.get("https://api.vk.com/method/wall.post", params=params) as retry_resp:
                            retry_result = await retry_resp.json()
                            if "error" in retry_result:
                                logging.error(f"❌ Повторная попытка не удалась: {retry_result['error']}")
                            else:
                                post_id = retry_result['response']['post_id']
                                logging.info(f"✅ Успешно опубликовано (повторная попытка)! Post ID: {post_id}")
                                # Сохраняем в историю
                                history["published"][post_hash] = post_id
                                history["last_post_time"] = current_time
                                save_published_history(history)
                else:
                    post_id = result['response']['post_id']
                    logging.info(f"✅ Успешно опубликовано в ВК! Post ID: {post_id}")
                    if link:
                        logging.info(f"🔗 Ссылка на оригинал: {link}")
                    # Сохраняем в историю
                    history["published"][post_hash] = post_id
                    history["last_post_time"] = current_time
                    save_published_history(history)

        except Exception as e:
            logging.error(f"❌ Ошибка при обработке поста: {e}", exc_info=True)
        finally:
            # Очистка временного файла
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                logging.info(f"🗑️ Временный файл удален: {file_path}")

async def main():
    logging.info("🚀 Бот запущен и слушает новые посты...")
    logging.info(f"📢 Отслеживаемый канал: {TG_CHANNEL_ID}")
    logging.info(f"👥 Группа ВК: {VK_GROUP_ID}")
    logging.info(f"🛡️ Защита от флуда: минимум {MIN_POST_INTERVAL} сек между постами")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
