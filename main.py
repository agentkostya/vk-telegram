import os
import asyncio
import logging
import aiohttp
import hashlib
import json
from aiogram import Bot, Dispatcher
from aiogram.types import Message

# ================= НАСТРОЙКИ (Только токен сообщества) =================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL_ID = str(os.getenv("TG_CHANNEL_ID", "")).strip()

VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = str(os.getenv("VK_GROUP_ID", "")).strip()
VK_API_VERSION = "5.199"

HISTORY_FILE = "published_posts.json"
# =======================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if not all([TG_BOT_TOKEN, TG_CHANNEL_ID, VK_TOKEN, VK_GROUP_ID]):
    logging.error("❌ Не найдены все необходимые переменные окружения! Проверьте секреты.")
    exit(1)

bot = Bot(token=TG_BOT_TOKEN)
dp = Dispatcher()

async def vk_api_request(session: aiohttp.ClientSession, method: str, params: dict, max_retries=3):
    """Универсальный запрос к VK API с автоматическим повтором при Flood Control (ошибка 9)"""
    for attempt in range(max_retries):
        async with session.get(f"https://api.vk.com/method/{method}", params=params) as resp:
            data = await resp.json()
            
            if "error" in data:
                error_code = data["error"].get("error_code")
                error_msg = data["error"].get("error_msg")
                
                # Если VK просит подождать, мы ждем и пробуем снова
                if error_code == 9:
                    wait_time = 15 * (attempt + 1)  # 15, 30, 45 секунд
                    logging.warning(f"⏳ VK Flood Control ({method}). Ждем {wait_time} сек и пробуем снова (попытка {attempt + 1}/{max_retries})...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Любая другая ошибка (например, 27) сразу прерывает выполнение
                    raise Exception(f"VK API Error {error_code}: {error_msg}")
            
            return data["response"]
            
    raise Exception(f"VK API: Превышено количество попыток из-за Flood Control")

def load_published_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"published": {}, "last_post_time": 0}

def save_published_history(history: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

def get_post_hash(message: Message) -> str:
    content = f"{message.chat.id}_{message.message_id}_{message.text or message.caption or ''}"
    return hashlib.md5(content.encode()).hexdigest()

async def upload_vk_photo(session: aiohttp.ClientSession, file_path: str) -> str:
    logging.info(f"📷 Начинаем загрузку фото в ВК...")
    
    # 1. Получаем URL для загрузки (используем group_id для токена сообщества)
    upload_data = await vk_api_request(session, "photos.getWallUploadServer", {
        "group_id": VK_GROUP_ID,
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION
    })
    
    # 2. Загружаем файл напрямую на сервер VK
    with open(file_path, "rb") as f:
        async with session.post(upload_data["upload_url"], data={"photo": f}) as resp:
            upload_result = await resp.json()

    # 3. Сохраняем фото на сервере ВК
    saved_data = await vk_api_request(session, "photos.saveWallPhoto", {
        "group_id": VK_GROUP_ID,
        "photo": upload_result["photo"],
        "server": upload_result["server"],
        "hash": upload_result["hash"],
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION
    })
    
    photo_obj = saved_data[0]
    return f"photo{photo_obj['owner_id']}_{photo_obj['id']}"

async def upload_vk_video(session: aiohttp.ClientSession, file_path: str) -> str:
    logging.info(f"🎥 Начинаем загрузку видео в ВК...")
    
    # 1. Получаем URL для загрузки видео
    video_data = await vk_api_request(session, "video.save", {
        "group_id": VK_GROUP_ID,
        "name": os.path.basename(file_path),
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION
    })
    
    # 2. Загружаем файл
    with open(file_path, "rb") as f:
        async with session.post(video_data["upload_url"], data={"file": f}) as resp:
            await resp.text()

    return f"video{video_data['owner_id']}_{video_data['id']}"

@dp.channel_post()
async def handle_channel_post(message: Message):
    # Реагируем ТОЛЬКО на посты из нужного канала
    if str(message.chat.id) != TG_CHANNEL_ID:
        return

    history = load_published_history()
    post_hash = get_post_hash(message)
    
    # Защита от дубликатов
    if post_hash in history["published"]:
        logging.info(f"⏭️ Этот пост уже был опубликован, пропускаем.")
        return

    logging.info(f"📨 Получен новый пост из канала. Начинаем обработку...")
    text = message.text or message.caption or ""
    attachments = []
    link = f"https://t.me/{message.chat.username}/{message.message_id}" if message.chat.username else ""

    async with aiohttp.ClientSession() as session:
        file_path = None
        try:
            # --- Обработка ФОТО ---
            if message.photo:
                photo = message.photo[-1]  # Берем лучшее качество
                file = await bot.get_file(photo.file_id)
                file_path = f"temp_{file.file_unique_id}.jpg"
                await bot.download_file(file.file_path, file_path)
                attachments.append(await upload_vk_photo(session, file_path))

            # --- Обработка ВИДЕО ---
            elif message.video:
                video = message.video
                file = await bot.get_file(video.file_id)
                file_path = f"temp_{file.file_unique_id}.mp4"
                await bot.download_file(file.file_path, file_path)
                attachments.append(await upload_vk_video(session, file_path))

            # --- Публикация на стене ВК ---
            params = {
                "owner_id": f"-{VK_GROUP_ID}",  # Для публикации в группе нужен минус
                "message": text,
                "attachments": ",".join(attachments) if attachments else "",
                "from_group": 1,                # Публикуем от имени группы
                "access_token": VK_TOKEN,
                "v": VK_API_VERSION
            }
            if link:
                params["copyright"] = link

            result = await vk_api_request(session, "wall.post", params)
            post_id = result['post_id']
            
            logging.info(f"✅ Успешно опубликовано в ВК! Post ID: {post_id}")
            
            # Сохраняем в историю, чтобы не дублировать
            history["published"][post_hash] = post_id
            history["last_post_time"] = asyncio.get_event_loop().time()
            save_published_history(history)

        except Exception as e:
            logging.error(f"❌ Критическая ошибка при обработке поста: {e}", exc_info=True)
        finally:
            # Всегда очищаем временный файл
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                logging.info(f"🗑️ Временный файл удален: {file_path}")

async def main():
    logging.info("🚀 Бот запущен и слушает новые посты...")
    logging.info(f"📢 Канал: {TG_CHANNEL_ID} | 👥 Группа ВК: {VK_GROUP_ID}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
