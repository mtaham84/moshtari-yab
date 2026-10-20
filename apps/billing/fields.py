from django.db import models

from need_engine.crypto import decrypt_secret, encrypt_secret


class EncryptedTextField(models.CharField):
    """A CharField that encrypts values before writing them and decrypts them after reading."""

    description = "Encrypted text"

    def from_db_value(self, value, expression, connection):
        return decrypt_secret(value)

    def to_python(self, value):
        if value is None:
            return value
        return decrypt_secret(value)

    def get_prep_value(self, value):
        # CharField normally calls to_python() here. This field's to_python()
        # decrypts stored tokens, so keep the write path explicit and encrypt
        # the caller's plaintext exactly once.
        if value is None:
            return None
        return encrypt_secret(str(value))

    def get_db_prep_value(self, value, connection, prepared=False):
        # get_prep_value() already encrypts the value for standard ORM paths.
        # QuerySet.update(..., prepared=True) passes its prepped value directly.
        return self.get_prep_value(value) if not prepared else value
