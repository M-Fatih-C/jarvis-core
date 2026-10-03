"""Encrypted, owner-scoped recent conversational context across Mac restarts."""
import json
from pathlib import Path
import sqlite3

from core.memory.crypto import MacKeychainKeyProvider, MemoryEncryptor
from core.models.messages import ChatMessage


class ConversationStore:
    def __init__(self, path, key_provider=None):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS conversations (owner TEXT, conversation TEXT, encrypted TEXT, PRIMARY KEY(owner, conversation))")
        self.connection.commit()
        self.keys = key_provider or MacKeychainKeyProvider(service_name="com.jarvis.conversations", username="history_key")

    @staticmethod
    def scope(owner, conversation):
        return json.dumps([owner, conversation], separators=(",", ":")).encode()

    async def load(self, owner, conversation):
        row = self.connection.execute("SELECT encrypted FROM conversations WHERE owner=? AND conversation=?", (owner, conversation)).fetchone()
        if not row:
            return []
        key = await self.keys.get_or_create_memory_key()
        plain = MemoryEncryptor.decrypt_raw(row[0], key, associated_data=self.scope(owner, conversation))
        return [ChatMessage.model_validate(item) for item in json.loads(plain)]

    async def save(self, owner, conversation, messages):
        key = await self.keys.get_or_create_memory_key()
        plain = json.dumps([m.model_dump(mode="json") for m in messages[-20:]], ensure_ascii=False)
        encrypted = MemoryEncryptor.encrypt(plain, key, associated_data=self.scope(owner, conversation))
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO conversations VALUES (?, ?, ?)", (owner, conversation, json.dumps(encrypted)))
