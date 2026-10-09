import os
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.types import Message

# ================= НАСТРОЙКИ (Читаются из GitHub Secrets / Переменных окружения) =================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHANNEL_ID = str(os.getenv("TG_CHANNEL_ID", "")).strip()

VK_TOKEN = os.getenv("VK_TOKEN")
VK_GROUP_ID = str(os.getenv("VK_GROUP_ID", "")).strip()
VK_API_VERSION = "5.199"
# ================================================================================================

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if not all([TG_BOT_TOKEN, TG_CHANNEL_ID, VK_TOKEN, VK_GROUP_ID]):
    logging.error("❌ Не найдены все необходимые переменные окружения! Проверьте секреты.")
    exit(1)

bot = Bot(token=TG_BOT_TOKEN)
dp = Dispatcher()

async def upload_vk_photo(session: aiohttp.ClientSession, file_path: str) -> str:
    """Загружает фото на стену ВК и возвращает строку attachment (photoX_Y)"""
    async with session.get("https://api.vk.com/method/photos.getWallUploadServer", 
                           params={"group_id": VK_GROUP_ID, "access_token": VK_TOKEN, "v": VK_API_VERSION}) as resp:
        data = await resp.json()
        if "error" in data:
            raise Exception(f"VK getWallUploadServer: {data['error']}")
        upload_url = data["response"]["upload_url"]

    with open(file_path, "rb") as f:
        async with session.post(upload_url, data={"photo": f}) as resp:
            upload_data = await resp.json()

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
            raise Exception(f"VK saveWallPhoto: {data['error']}")
        photo_obj = data["response"][0]
        return f"photo{photo_obj['owner_id']}_{photo_obj['id']}"

async def upload_vk_video(session: aiohttp.ClientSession, file_path: str) -> str:
    """Загружает видео в группу ВК и возвращает строку attachment (videoX_Y)"""
    async with session.get("https://api.vk.com/method/video.save", 
                           params={
                               "group_id": VK_GROUP_ID,
                               "name": os.path.basename(file_path),
                               "access_token": VK_TOKEN,
                               "v": VK_API_VERSION
                           }) as resp:
        data = await resp.json()
        if "error" in data:
            raise Exception(f"VK video.save: {data['error']}")
        video_data = data["response"]
        upload_url = video_data["upload_url"]

    with open(file_path, "rb") as f:
        async with session.post(upload_url, data={"file": f}) as resp:
            await resp.text() 

    return f"video{video_data['owner_id']}_{video_data['id']}"

@dp.channel_post()
async def handle_channel_post(message: Message):
    # Фильтр: реагируем ТОЛЬКО на посты из нужного канала
    if str(message.chat.id) != TG_CHANNEL_ID:
        return

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
                "owner_id": f"-{VK_GROUP_ID}",
                "message": text,
                "attachments": ",".join(attachments) if attachments else "",
                "from_group": 1,
                "access_token": VK_TOKEN,
                "v": VK_API_VERSION
            }
            if link:
                params["copyright"] = link

            async with session.get("https://api.vk.com/method/wall.post", params=params) as resp:
                result = await resp.json()
                if "error" in result:
                    logging.error(f"❌ Ошибка VK API: {result['error']}")
                else:
                    logging.info(f"✅ Успешно опубликовано в ВК! Post ID: {result['response']['post_id']}")

        except Exception as e:
            logging.error(f"❌ Ошибка при обработке поста: {e}")
        finally:
            # Очистка временного файла
            if file_path and os.path.exists(file_path):
                os.remove(file_path)

async def main():
    logging.info("🚀 Бот запущен и слушает новые посты...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
