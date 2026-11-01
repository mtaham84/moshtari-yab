import logging
from unittest.mock import MagicMock, patch
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.test import RequestFactory, TestCase

from apps.businesses.models import Business
from apps.core.llm import LLMError, chat_json
from apps.core.typed_errors import (
    ApplicationError,
    ConflictError,
    ErrorCode,
    PermissionDeniedError,
    TransientError,
    ValidationError,
    log_sanitized,
    sanitize_data,
    sanitize_text,
)


class TestTypedErrorsAndSanitization(TestCase):
    def test_sanitize_text_redacts_credentials(self):
        text_with_secrets = (
            "Upstream failed with key: sk-abcdef1234567890abcdef1234567890 "
            "and bearer Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz "
            "with password='my_super_secret_password' "
            "and connection postgresql://dbuser:supersecretpass@db.internal:5432/mydb"
        )
        cleaned = sanitize_text(text_with_secrets)
        self.assertNotIn("sk-abcdef1234567890", cleaned)
        self.assertNotIn("my_super_secret_password", cleaned)
        self.assertNotIn("supersecretpass", cleaned)
        self.assertIn("[REDACTED_API_KEY]", cleaned)
        self.assertIn("[REDACTED_PASSWORD]", cleaned)

    def test_sanitize_data_recursively_masks_keys(self):
        data = {
            "user": "test_user",
            "password": "plain_password_123",
            "metadata": {
                "api_key": "sk-secretkey1234567890",
                "nested_token": "token_val",
                "safe_field": 42,
            },
            "unrelated_list": ["Bearer token123456789012345"],
        }
        cleaned = sanitize_data(data)
        self.assertEqual(cleaned["password"], "[REDACTED]")
        self.assertEqual(cleaned["metadata"]["api_key"], "[REDACTED]")
        self.assertEqual(cleaned["metadata"]["safe_field"], 42)
        self.assertIn("[REDACTED_TOKEN]", cleaned["unrelated_list"][0])

    def test_application_error_typed_hierarchy(self):
        err = ValidationError("Bad input: sessionid=123456789")
        self.assertEqual(err.code, ErrorCode.VALIDATION)
        self.assertNotIn("123456789", err.message)
        self.assertIn("[REDACTED]", err.message)
        d = err.to_dict()
        self.assertEqual(d["error_code"], "validation_error")

        conf = ConflictError("Duplicate entry")
        self.assertEqual(conf.code, ErrorCode.CONFLICT)

        perm = PermissionDeniedError("Forbidden")
        self.assertEqual(perm.code, ErrorCode.PERMISSION)

        trans = TransientError("Timeout")
        self.assertEqual(trans.code, ErrorCode.TRANSIENT)

    def test_log_sanitized_redacts_context(self):
        logger_mock = MagicMock()
        log_sanitized(
            logger_mock,
            logging.ERROR,
            "test_event",
            tenant_id="tenant_123",
            error=ValueError("Failed connecting postgres://user:mypassword@localhost:5432/db"),
            extra={"auth_token": "Bearer token123456789012345"},
        )
        logger_mock.log.assert_called_once()
        args, _ = logger_mock.log.call_args
        log_msg = args[1]
        self.assertNotIn("mypassword", log_msg)
        self.assertNotIn("token123456789012345", log_msg)
        self.assertIn("[REDACTED_PASSWORD]", log_msg)

    def test_llm_call_sanitizes_upstream_exception(self):
        from apps.core.gateway_contract import GatewayResult, GatewayResultStatus
        from apps.core.llm import set_gateway_override

        mock_gw = MagicMock()
        mock_gw.execute.return_value = GatewayResult(
            status=GatewayResultStatus.FAILED,
            error_message="Invalid request: key sk-12345678901234567890 was rejected by provider",
        )
        set_gateway_override(mock_gw)
        self.addCleanup(set_gateway_override, None)

        with self.assertRaises(LLMError) as cm:
            chat_json(system="sys", user="usr", purpose="test")

        err_str = str(cm.exception)
        self.assertNotIn("sk-12345678901234567890", err_str)
        self.assertIn("[REDACTED_API_KEY]", err_str)

    def test_products_view_error_sanitization(self):
        from apps.products.views import product_add_view
        from django.contrib.auth import get_user_model
        from django.contrib.messages.storage.fallback import FallbackStorage
        from apps.products.models import Category

        User = get_user_model()
        user, _ = User.objects.get_or_create(username="test_seller", defaults={"email": "s@example.com"})
        biz, _ = Business.objects.get_or_create(user=user, name="Seller Store")
        cat, _ = Category.objects.get_or_create(name="Test Category")

        factory = RequestFactory()
        req = factory.post("/products/add/", data={
            "name": "Test Product",
            "price": "1000",
            "description": "Valid product description for testing",
            "product_type": "PHYSICAL",
            "category_id": str(cat.id),
        })
        req.user = user

        # Attach messages storage
        setattr(req, "session", {})
        messages = FallbackStorage(req)
        setattr(req, "_messages", messages)

        # Mock Product.save to raise an IntegrityError containing sensitive SQL table details
        with patch("apps.products.models.Product.save", side_effect=IntegrityError("duplicate key value violates unique constraint 'idx_secret_token'")):
            resp = product_add_view(req)
            self.assertEqual(resp.status_code, 200)

            # Ensure flash messages don't expose raw SQL error
            msg_texts = [str(m) for m in messages]
            self.assertTrue(any("داده تکراری یا تداخل" in m for m in msg_texts))
            self.assertFalse(any("idx_secret_token" in m for m in msg_texts))
