# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Sensitive environment settings and local .env loading helpers.

Classes: SensitiveConfig.
Functions: load_env_file.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, cast

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .secret_envelope import decrypt_env_blob

_current_dir = Path(__file__).parent
PROJECT_ROOT = _current_dir.parent
ENV_PATH = PROJECT_ROOT / "config/.env"
ENCRYPTED_ENV_PATH = PROJECT_ROOT / "config/.env.encrypted"
LICENSE_KEY_ENV = "PHYTOMNI_LICENSE_KEY"
# Model A delivery: the per-customer key is never baked into the
# image. The customer drops it here at deploy time (or mounts it as a
# Docker volume / k8s secret). MUST stay out of git and image build
# contexts — see .gitignore / .dockerignore.
LICENSE_KEY_PATH = PROJECT_ROOT / "config/.license_key"

# In-process, never-persisted memo so the PBKDF2 decrypt runs once
# per process even though SensitiveConfig.load() is called at import
# time in 9+ modules. Tests reset it via monkeypatch.
_ENV_DECRYPT_MEMO = {"done": False}


def _resolve_license_key() -> str | None:
    """Resolve the per-customer license key (Model A delivery).

    Two sources may provide the key: the ``PHYTOMNI_LICENSE_KEY``
    environment variable (operator / ``docker -e``) and the
    out-of-band file the customer drops at ``LICENSE_KEY_PATH``. The
    decrypted ``.env`` contents never leave the process; this only
    decides where the *key* comes from.

    Returns:
        The license key, or ``None`` when no source supplies one (the
        caller then falls through to the plaintext / ``RuntimeError``
        paths exactly as before).
    """
    # 1. Env var wins, mirroring the os.environ.setdefault
    #    "env wins over blob" rule: an operator can `docker -e`
    #    override without re-shipping the key file. A set-but-blank
    #    var is treated as unset so resolution still falls through.
    env_value = os.getenv(LICENSE_KEY_ENV)
    if env_value and env_value.strip():
        return env_value.strip()
    if LICENSE_KEY_PATH.exists():
        # 4. Fail loud on an unreadable key file: a swallowed
        #    PermissionError would fall through to the generic
        #    "no configuration source" RuntimeError and mask the
        #    real operator misconfiguration. read_text raises
        #    PermissionError (an OSError) and we let it propagate.
        # 2. Strip the trailing newline `echo`/editors add, else
        #    PBKDF2 fails with a confusing SecretEnvelopeError.
        file_value = LICENSE_KEY_PATH.read_text(encoding="utf-8")
        # 3. Empty / whitespace-only file => no key, fall through.
        if file_value.strip():
            return file_value.strip()
    return None


def load_env_file() -> bool:
    """Load and validate environment variables from .env file.

    Checks for the existence of the .env file and raises an error if not found.
    Automatically loads environment variables into the application context.

    Resolution order:
        1. ``PHYTOMNI_TESTING=1`` — tests inject dummy secrets; no
           file is read.
        2. A license key (``PHYTOMNI_LICENSE_KEY`` env var or the
           ``LICENSE_KEY_PATH`` file, resolved by
           ``_resolve_license_key``) and an encrypted envelope at
           ``ENCRYPTED_ENV_PATH`` — decrypt once per process and
           inject into ``os.environ`` (existing env wins).
        3. A plaintext ``ENV_PATH`` — load it (file wins, legacy
           developer behaviour).

    Raises:
        SecretEnvelopeError: If the encrypted envelope cannot be
            opened (wrong license key or corrupted file). Propagated
            uncaught so the process refuses to start rather than
            booting with empty secrets.
        RuntimeError: If none of the three provisioning paths apply.

    Returns:
        bool: True if environment variables were successfully loaded
    """
    if os.getenv("PHYTOMNI_TESTING") == "1":
        return True
    license_key = _resolve_license_key()
    if license_key and ENCRYPTED_ENV_PATH.exists():
        if not _ENV_DECRYPT_MEMO["done"]:
            decrypted = decrypt_env_blob(
                ENCRYPTED_ENV_PATH.read_bytes(), license_key
            )
            for key, value in decrypted.items():
                os.environ.setdefault(key, value)
            decrypted.clear()
            _ENV_DECRYPT_MEMO["done"] = True
        return True
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)
        return True
    raise RuntimeError(
        "No configuration source found. Provide exactly one of:\n"
        "  1. PHYTOMNI_TESTING=1 (test suites inject dummy secrets)\n"
        f"  2. {LICENSE_KEY_ENV}=<license-key> (or the key in "
        f"{LICENSE_KEY_PATH}) with an encrypted envelope at "
        f"{ENCRYPTED_ENV_PATH}\n"
        f"  3. a plaintext .env at {ENV_PATH} (copy "
        f"{PROJECT_ROOT}/config/.env.example)"
    )


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
        """Return the process-cached SensitiveConfig instance.

        Delegates to ``get_sensitive_config()`` so callers across the
        codebase share one decrypted bundle per process even when they
        invoke ``SensitiveConfig.load()`` many times. Tests that mutate
        env vars between calls must drop the cache via
        ``get_sensitive_config.cache_clear()`` (the autouse fixture in
        ``tests/conftest.py`` does this between every test).

        Returns:
            SensitiveConfig: Fully populated configuration instance.

        Raises:
            ValidationError: If any required fields are missing or invalid.
        """
        return get_sensitive_config()

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


@lru_cache(maxsize=1)
def get_sensitive_config() -> SensitiveConfig:
    """Return a process-cached ``SensitiveConfig`` instance.

    The first call performs the full ``load_env_file()`` chain (which
    decrypts ``.env.encrypted`` once or reads plaintext ``.env``), then
    instantiates ``SensitiveConfig``. Subsequent calls return the same
    instance from the in-process ``lru_cache`` so the 17+ callers
    across the codebase do not each re-run the load/decode pipeline.
    Tests reset the cache via ``get_sensitive_config.cache_clear()``;
    the autouse fixture in ``tests/conftest.py`` runs this between
    every test to keep monkeypatched env-var assertions independent.
    """
    load_env_file()
    settings_cls = cast(Any, SensitiveConfig)
    if os.getenv("PHYTOMNI_TESTING") == "1":
        return settings_cls(_env_file=None)
    # Match load_env_file's resolution: either source (env var or
    # the on-disk LICENSE_KEY_PATH file) counts as a license key.
    # The previous os.getenv-only check missed the file-only Model
    # A path, letting a stray plaintext .env shadow the decrypted
    # envelope through the class-bound env_file=ENV_PATH default.
    license_key = _resolve_license_key()
    if license_key and ENCRYPTED_ENV_PATH.exists():
        # Encrypted values are already in os.environ; the
        # class-bound env_file=ENV_PATH must be ignored so a
        # stray plaintext .env cannot leak in.
        return settings_cls(_env_file=None)
    return settings_cls()
