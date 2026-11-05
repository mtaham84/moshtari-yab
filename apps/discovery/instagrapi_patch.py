"""
Runtime compatibility patch for instagrapi library.
Resolves the deprecated /api/v1/qe/sync/ endpoint 404 error by fetching
password encryption public keys from active Meta endpoints.
"""
import logging

logger = logging.getLogger("discovery.instagrapi_patch")


def patch_instagrapi_password_keys():
    """Patches PasswordMixin.password_publickeys to use active Instagram endpoints."""
    try:
        from instagrapi.mixins.password import PasswordMixin

        # Guard against double-patching
        if getattr(PasswordMixin, "_patched_for_meta_endpoints", False):
            return

        def _patched_password_publickeys(self):
            for ep in [
                "https://i.instagram.com/api/v1/launcher/sync/",
                "https://i.instagram.com/api/v1/accounts/login/",
                "https://i.instagram.com/api/v1/qe/sync/",
            ]:
                try:
                    resp = self.public.get(ep, timeout=10)
                    key_id = resp.headers.get("ig-set-password-encryption-key-id")
                    key_pub = resp.headers.get("ig-set-password-encryption-pub-key")
                    if key_id and key_pub:
                        return int(key_id), key_pub
                except Exception:
                    continue
            raise RuntimeError("Could not retrieve Instagram password encryption public keys from Meta endpoints.")

        PasswordMixin.password_publickeys = _patched_password_publickeys
        PasswordMixin._patched_for_meta_endpoints = True
        logger.debug("Successfully patched instagrapi password encryption key endpoints.")
    except Exception as exc:
        logger.warning("Could not patch instagrapi password keys: %s", exc)


# Apply immediately upon module import
patch_instagrapi_password_keys()
