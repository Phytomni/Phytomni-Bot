# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Sensitive environment settings and local .env loading helpers.

Classes: SensitiveConfig.
Functions: load_env_file, generate_env_template.
"""

import os
from pathlib import Path
from typing import Annotated, Any, cast

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_current_dir = Path(__file__).parent
PROJECT_ROOT = _current_dir.parent
ENV_PATH = PROJECT_ROOT / "config/.env"


def load_env_file() -> bool:
    """Load and validate environment variables from .env file.

    Checks for the existence of the .env file and raises an error if not found.
    Automatically loads environment variables into the application context.

    Raises:
        FileNotFoundError: If the .env file is missing and cannot be generated
        RuntimeError: If required environment variables are missing

    Returns:
        bool: True if environment variables were successfully loaded
    """
    if os.getenv("PHYTOMNI_TESTING") == "1":
        return True
    if not ENV_PATH.exists():
        generate_env_template()
        raise FileNotFoundError(
            "Missing .env file. "
            f"Please create using {PROJECT_ROOT}/config/.env.example"
        )
    load_dotenv(ENV_PATH, override=True)
    return True


def generate_env_template() -> None:
    """Generate a .env.example template file with default configuration.

    Creates a template file containing all required environment variables
    with placeholder values and example formatting.

    Returns:
        None. The template file is created only when it does not exist.
    """
    template = """# Required configuration (⚠️ remove comments)
DOMAIN_NAME=your_domain_name
USER_NAME=your_username
USER_PASSWORD=your_password
ACCESS_KEY_ID=your_access_key_id
SECRET_ACCESS_KEY=your_secret_access_key
BASE_URL=your_base_url
MODEL_ID=your_model_id
API_KEY=your_api_key
CODER_URL=your_coder_url
CODER_MODEL=your_coder_model
CODER_API_KEY=your_coder_api_key
BI_TOKEN=your_bi_token
"""
    env_example = Path(__file__).parent.parent / ".env.example"
    if not env_example.exists():
        with open(env_example, "w", encoding="utf-8") as f:
            f.write(template)
        print(f"Template generated: {env_example}")


class SensitiveConfig(BaseSettings):
    """Configuration model for sensitive environment variables.

    This class defines settings that are typically sensitive, such as
    credentials and API keys, and loads them from environment variables.

    Attributes:
        DOMAIN_NAME (str): Authentication domain name, used for services
            requiring domain-specific authentication.
        USER_NAME (str): System username for API access or service
            authentication.
        USER_PASSWORD (SecretStr): Encrypted user password or credentials for
            API access or service authentication. `SecretStr` helps prevent
            accidental exposure.
        BASE_URL (str): Base URL for a primary API service, typically for a
            large language model or a core platform API.
        MODEL_ID (str): Identifier for the specific model to be used with the
            service at `BASE_URL`.
        API_KEY (SecretStr): Encrypted API key for accessing the service at
            `BASE_URL`. `SecretStr` helps prevent accidental exposure.
        CODER_URL (str): Base URL for a code generation API service.
        CODER_MODEL (str): Identifier for the AI model version to be used with
            the code generation service at `CODER_URL`.
        CODER_API (SecretStr): Encrypted API key for accessing the code
            generation service at `CODER_URL`. `SecretStr` helps prevent
            accidental exposure.
        BI_TOKEN (SecretStr): Optional token for direct biological database
            SQL API access.
    """

    DOMAIN_NAME: str
    USER_NAME: str
    USER_PASSWORD: SecretStr
    ACCESS_KEY_ID: Annotated[
        SecretStr,
        Field(validation_alias=AliasChoices("ACCESS_KEY_ID", "AccessKeyID")),
    ]
    SECRET_ACCESS_KEY: Annotated[
        SecretStr,
        Field(
            validation_alias=AliasChoices(
                "SECRET_ACCESS_KEY",
                "SecretAccessKey",
            )
        ),
    ]
    BASE_URL: str
    MODEL_ID: str
    API_KEY: SecretStr
    CODER_URL: str
    CODER_MODEL: str
    CODER_API_KEY: SecretStr
    BI_TOKEN: SecretStr = SecretStr("")
    EMBED_URL: str
    EMBED_MODEL: str
    EMBED_API_KEY: SecretStr
    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
    )

    @classmethod
    def load(cls) -> "SensitiveConfig":
        """Initialize configuration by loading environment variables.

        Returns:
            SensitiveConfig: Fully populated configuration instance

        Raises:
            ValidationError: If any required fields are missing or invalid
        """
        load_env_file()
        settings_cls = cast(Any, cls)
        if os.getenv("PHYTOMNI_TESTING") == "1":
            return settings_cls(_env_file=None)
        return settings_cls()

    def obs_credentials(self) -> tuple[str, str]:
        """Return OBS access and secret access key values.

        Returns:
            Tuple containing the plain OBS access key ID and secret access
            key values.
        """
        return (
            self.ACCESS_KEY_ID.get_secret_value(),
            self.SECRET_ACCESS_KEY.get_secret_value(),
        )
