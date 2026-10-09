"""
Telegram -> VK: публикует последний пост Telegram-канала на стену группы ВКонтакте.

Запуск: python telegram_to_vk.py

Необходимые переменные окружения (задаются через GitHub Secrets):
    TG_API_ID        — api_id из https://my.telegram.org
    TG_API_HASH      — api_hash из https://my.telegram.org
    TG_SESSION       — строка сессии Telethon (см. README, раздел «Получение сессии»)
    TG_CHANNEL       — @username канала или его числовой id
    VK_TOKEN         — токен сообщества с правами wall и video
    VK_GROUP_ID      — id группы ВК (только цифры, без минуса)
    VK_FROM_GROUP    — 1 = от имени группы, 0 = от имени администратора (по умолчанию 1)
    VK_COPYRIGHT     — (опционально) ссылка-источник, прикрепляется к посту ВК
"""

import os
import shutil
import sys
import tempfile

import requests
from telethon import TelegramClient

VK_API = "https://api.vk.com/method"
VK_API_VERSION = "5.199"

# В Telethon сессию можно передавать строкой
SESSION_STRING = os.environ.get("TG_SESSION", "")


def get_last_telegram_post(channel: str):
    """Возвращает (text, media_kind, media_path, link) последнего поста канала.

    media_kind: None, "photo" или "video"; media_path — временный файл, который
    нужно удалить после загрузки.
    """
    client = TelegramClient(
        SESSION_STRING if SESSION_STRING else "anon",
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    tmpdir = tempfile.mkdtemp(prefix="tg2vk_")
    with client:
        message = None
        for msg in client.iter_messages(channel, limit=20):
            if msg.message or msg.media:  # пропускаем служебные/пустые
                message = msg
                break
        if message is None:
            raise RuntimeError(f"В канале {channel} не найдено ни одного поста.")

        text = message.message or ""
        link = f"https://t.me/{channel.lstrip('@')}/{message.id}" if channel.startswith("@") else ""

        media_kind = None
        media_path = None
        if message.photo:
            media_kind = "photo"
        elif message.video:
            media_kind = "video"
        if media_kind:
            media_path = client.download_media(message.media, file=tmpdir)
        return text, media_kind, media_path, link, tmpdir


def vk_upload_photo(group_id: str, token: str, photo_path: str) -> str:
    """Загружает фото на стену группы и возвращает attachment-строку photo<owner>_<id>."""
    resp = requests.get(
        f"{VK_API}/photos.getWallUploadServer",
        params={"group_id": group_id, "access_token": token, "v": VK_API_VERSION},
        timeout=30,
    ).json()
    if "error" in resp:
        raise RuntimeError(f"VK getWallUploadServer: {resp['error']}")

    with open(photo_path, "rb") as f:
        upload = requests.post(
            resp["response"]["upload_url"],
            files={"photo": f},
            timeout=120,
        ).json()

    save = requests.get(
        f"{VK_API}/photos.saveWallPhoto",
        params={
            "group_id": group_id,
            "photo": upload["photo"],
            "server": upload["server"],
            "hash": upload["hash"],
            "access_token": token,
            "v": VK_API_VERSION,
        },
        timeout=30,
    ).json()
    if "error" in save:
        raise RuntimeError(f"VK saveWallPhoto: {save['error']}")

    photo_obj = save["response"][0]
    return f"photo{photo_obj['owner_id']}_{photo_obj['id']}"


def vk_upload_video(group_id: str, token: str, video_path: str) -> str:
    """Загружает видео в видеозаписи группы и возвращает attachment-строку video<owner>_<id>."""
    resp = requests.get(
        f"{VK_API}/video.save",
        params={
            "group_id": group_id,
            "name": os.path.basename(video_path),
            "access_token": token,
            "v": VK_API_VERSION,
        },
        timeout=30,
    ).json()
    if "error" in resp:
        raise RuntimeError(f"VK video.save: {resp['error']}")

    video = resp["response"]
    with open(video_path, "rb") as f:
        upload = requests.post(
            video["upload_url"],
            files={"file": f},
            timeout=900,  # видео может быть тяжёлым
        ).json()
    if "error" in upload:
        raise RuntimeError(f"VK video upload: {upload['error']}")

    return f"video{video['owner_id']}_{video['id']}"


def vk_post_wall(text: str, attachments: list[str], link: str) -> None:
    token = os.environ["VK_TOKEN"]
    group_id = os.environ["VK_GROUP_ID"]

    params = {
        "owner_id": f"-{group_id}",
        "message": text,
        "attachments": ",".join(attachments) if attachments else "",
        "from_group": os.environ.get("VK_FROM_GROUP", "1"),
        "access_token": token,
        "v": VK_API_VERSION,
    }
    copyright_link = os.environ.get("VK_COPYRIGHT") or link
    if copyright_link:
        params["copyright"] = copyright_link

    resp = requests.get(f"{VK_API}/wall.post", params=params, timeout=30).json()
    if "error" in resp:
        raise RuntimeError(f"VK wall.post: {resp['error']}")
    print(f"Опубликовано в ВК, post_id={resp['response']['post_id']}")


def main() -> None:
    required = ["TG_API_ID", "TG_API_HASH", "TG_CHANNEL", "VK_TOKEN", "VK_GROUP_ID"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        sys.exit(f"Не заданы переменные окружения: {', '.join(missing)}")

    text, media_kind, media_path, link, tmpdir = get_last_telegram_post(os.environ["TG_CHANNEL"])
    print(f"Найден пост из Telegram ({len(text)} символов, вложение: {media_kind or 'нет'})")

    try:
        attachments = []
        if media_kind == "photo":
            attachments.append(vk_upload_photo(os.environ["VK_GROUP_ID"], os.environ["VK_TOKEN"], media_path))
        elif media_kind == "video":
            attachments.append(vk_upload_video(os.environ["VK_GROUP_ID"], os.environ["VK_TOKEN"], media_path))
        vk_post_wall(text, attachments, link)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
