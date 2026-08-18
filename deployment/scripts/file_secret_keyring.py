"""Read-only Xiaomi secret backend for production deployment."""

from __future__ import annotations

import os
from pathlib import Path

import keyring.backend
import keyring.errors


SERVICE = "mi-fitness-mcp"
USER_ID_KEY = "mi_fitness_auth_user_id"
PASS_TOKEN_KEY = "mi_fitness_auth_pass_token"


class FileSecretKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def _read_secret(self, env_name: str, default_path: str) -> str | None:
        path = Path(os.environ.get(env_name, default_path))

        if not path.exists():
            return None

        value = path.read_text(encoding="utf-8").strip()

        return value or None

    def get_password(self, service: str, username: str) -> str | None:
        if service != SERVICE:
            return None

        if username == USER_ID_KEY:
            return self._read_secret(
                "PHE_XIAOMI_USER_ID_FILE",
                "/etc/phe/secrets/xiaomi_user_id",
            )

        if username == PASS_TOKEN_KEY:
            return self._read_secret(
                "PHE_XIAOMI_PASS_TOKEN_FILE",
                "/etc/phe/secrets/xiaomi_pass_token",
            )

        return None

    def set_password(
        self,
        service: str,
        username: str,
        password: str,
    ) -> None:
        raise keyring.errors.PasswordSetError(
            "production Xiaomi secret backend is read-only"
        )

    def delete_password(
        self,
        service: str,
        username: str,
    ) -> None:
        raise keyring.errors.PasswordDeleteError(
            "production Xiaomi secret backend is read-only"
        )
