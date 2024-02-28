from typing import Annotated, Final

from aiotgbot.helpers import BotKey
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ("SETTINGS_KEY", "Settings")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(secrets_dir="/run/secrets")

    admin_username: Annotated[str, Field(pattern="[A-Za-z][A-Za-z0-9_]{4,31}")]
    chat_list_size: Annotated[int, Field(ge=1, le=20)]
    tg_token: Annotated[SecretStr, Field(min_length=1)]


SETTINGS_KEY: Final = BotKey("settings", Settings)
