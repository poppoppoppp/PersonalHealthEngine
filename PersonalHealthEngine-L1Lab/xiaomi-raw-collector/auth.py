from dataclasses import dataclass

import keyring


KEYRING_SERVICE = "mi-fitness-mcp"
KEYRING_USER_ID = "mi_fitness_auth_user_id"
KEYRING_PASS_TOKEN = "mi_fitness_auth_pass_token"


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class XiaomiCredentials:
    user_id: str
    pass_token: str


def load_xiaomi_credentials() -> XiaomiCredentials:
    """
    Read the already-configured Xiaomi credentials from the OS keyring.

    Credentials are never printed or written to capture files.
    """
    try:
        user_id = keyring.get_password(
            KEYRING_SERVICE,
            KEYRING_USER_ID,
        )

        pass_token = keyring.get_password(
            KEYRING_SERVICE,
            KEYRING_PASS_TOKEN,
        )

    except Exception as exc:
        raise CredentialError(
            "Unable to read Xiaomi credentials from OS keyring."
        ) from exc

    if not user_id or not pass_token:
        raise CredentialError(
            "Xiaomi credentials are missing from OS keyring."
        )

    return XiaomiCredentials(
        user_id=user_id,
        pass_token=pass_token,
    )
