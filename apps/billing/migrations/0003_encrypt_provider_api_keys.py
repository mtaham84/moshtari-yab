from django.db import migrations
from django.db import models
from apps.billing.fields import EncryptedTextField


def encrypt_existing_keys(apps, schema_editor):
    from need_engine.crypto import encrypt_secret, is_encrypted
    table = schema_editor.quote_name("billing_provider")
    column = schema_editor.quote_name("api_key")
    primary_key = schema_editor.quote_name("id")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT {primary_key}, {column} FROM {table}")
        for provider_id, stored_value in cursor.fetchall():
            if stored_value and not is_encrypted(stored_value):
                cursor.execute(f"UPDATE {table} SET {column}=%s WHERE {primary_key}=%s",
                               [encrypt_secret(stored_value), provider_id])


def decrypt_existing_keys(apps, schema_editor):
    from need_engine.crypto import decrypt_secret, is_encrypted
    table = schema_editor.quote_name("billing_provider")
    column = schema_editor.quote_name("api_key")
    primary_key = schema_editor.quote_name("id")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT {primary_key}, {column} FROM {table}")
        for provider_id, stored_value in cursor.fetchall():
            if stored_value and is_encrypted(stored_value):
                cursor.execute(f"UPDATE {table} SET {column}=%s WHERE {primary_key}=%s",
                               [decrypt_secret(stored_value), provider_id])


class Migration(migrations.Migration):
    dependencies = [("billing", "0002_initial_data")]

    operations = [
        migrations.AlterField(
            model_name="provider",
            name="api_key",
            field=EncryptedTextField(blank=True, max_length=12000, verbose_name="API Key"),
        ),
        migrations.RunPython(encrypt_existing_keys, decrypt_existing_keys),
    ]
