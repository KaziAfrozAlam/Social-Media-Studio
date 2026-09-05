import logging
import uuid

import requests

from app.adapters.base import SocialPublisher, PublishResult

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot"


class TelegramPublisher(SocialPublisher):
    platform = "telegram"

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token
        self.chat_id = chat_id

    def publish(self, content: str, idempotency_key: str) -> PublishResult:
        if not self.token or not self.chat_id:
            raise RuntimeError(
                "TelegramPublisher is not configured: TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID must be set in .env"
            )
        url = f"{API_BASE}{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": content,
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Telegram API error {resp.status_code}: {resp.text[:500]}"
            )
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API refused: {body.get('description')}")

        message = body["result"]
        message_id = message["message_id"]
        chat = message.get("chat", {})
        username = chat.get("username", "")
        chat_id = self.chat_id
        if username:
            link = f"https://t.me/{username}/{message_id}"
        elif str(chat_id).lstrip("-").isdigit():
            link = f"https://t.me/c/{chat_id}/{message_id}"
        else:
            link = None

        return PublishResult(
            external_id=f"tg-{message_id}",
            message_url=link,
            raw=f"ok={body['ok']} message_id={message_id} chat={chat_id}",
        )