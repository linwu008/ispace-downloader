from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


SERVICE = "BNBU.iSpaceDownloader.v0.1"


class LoginRequired(Exception):
    pass


class Vault:
    """No plaintext fallback: credentials and the session encryption key live in Windows Vault."""

    def __init__(self, directory: Path):
        self.path = directory / "session.enc"

    def backend(self):
        if os.name != "nt":
            raise RuntimeError("凭据保存仅支持 Windows；测试应注入模拟凭据存储")
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    def credentials(self):
        raw = self.backend().get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else None

    def save_credentials(self, username, password):
        self.backend().set_password(SERVICE, "credentials", json.dumps({"username": username, "password": password}))

    def cipher(self):
        backend = self.backend()
        key = backend.get_password(SERVICE, "session-key")
        if not key:
            key = Fernet.generate_key().decode()
            backend.set_password(SERVICE, "session-key", key)
        return Fernet(key.encode())

    def load_session(self):
        if not self.path.exists():
            return None
        try:
            return json.loads(self.cipher().decrypt(self.path.read_bytes()))
        except (InvalidToken, ValueError):
            self.path.unlink(missing_ok=True)
            return None

    def save_session(self, state):
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(self.cipher().encrypt(json.dumps(state).encode()))
        temp.replace(self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)
        backend = self.backend()
        for name in ("credentials", "session-key"):
            if backend.get_password(SERVICE, name):
                backend.delete_password(SERVICE, name)
