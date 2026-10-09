# Telegram → VK

Публикует последний пост Telegram-канала (текст + фото или видео) на стену группы ВКонтакте.
Управление — вручную кнопкой в GitHub Actions (или по расписанию через cron).

## Что нужно получить заранее

### 1. Доступ Telegram (Telethon)

1. Зайди на https://my.telegram.org → **API development tools** → создай приложение.
   Сохрани `api_id` (число) и `api_hash`.
2. Локально один раз сгенерируй строку сессии (нужен будет код из Telegram):

   ```bash
   pip install telethon
   python -c "from telethon.sync import TelegramClient; from telethon.sessions import StringSession; print(StringSession.save(TelegramClient(StringSession(), api_id, api_hash).start()))"
   ```

   Подставь свои `api_id` и `api_hash`. На выходе — длинная строка, это и есть `TG_SESSION`.
   Аккаунт, от которого сделана сессия, должен иметь доступ к каналу (быть подписан / состоять в нём).

### 2. Токен сообщества ВК

1. Создай/открой сообщество → **Управление → Работа с API → Создать токен**,
   отметь права **«Управление сообществом»** (wall) и **«Видеозаписи»** (video — нужно для загрузки видео).
2. Узнай цифровой id группы (в адресной строке: `vk.com/club123456` → `123456`).

## Установка

1. Загрузи этот репозиторий на GitHub.
2. В репозитории: **Settings → Secrets and variables → Actions → New repository secret**,
   создай секреты:

   | Секрет         | Значение                                    |
   | -------------- | ------------------------------------------- |
   | `TG_API_ID`    | число из my.telegram.org                    |
   | `TG_API_HASH`  | hash из my.telegram.org                     |
   | `TG_SESSION`   | строка сессии из шага 1                     |
   | `TG_CHANNEL`   | `@имя_канала` (например `@mychannel`)       |
   | `VK_TOKEN`     | токен сообщества                            |
   | `VK_GROUP_ID`  | id группы цифрами, без минуса               |
   | `VK_FROM_GROUP`| `1` — от имени группы (опционально)         |
   | `VK_COPYRIGHT` | ссылка на источник, если нужна (опционально)|

3. Запуск: вкладка **Actions → «Отправить последний пост Telegram в ВК» → Run workflow**.

По умолчанию к посту ВК прикрепляется ссылка «источник» на оригинал в Telegram.
Чтобы выключить — удали `VK_COPYRIGHT` и в `telegram_to_vk.py` убери строку `or link`.

## Локальный запуск (для проверки)

Задай те же переменные окружения и выполни:

```bash
pip install -r requirements.txt
python telegram_to_vk.py
```

## Важно

- Сессия Telethon — это доступ к твоему Telegram-аккаунту. Храни её только в Secrets,
  никогда не коммить в репозиторий.
- Если Telegram запросит подтверждение входа (новая страна/устройство — GitHub-серверы),
  зайди в Telegram → Настройки → Устройства → подтверди сессию.
