"""Bot commands to list, show, edit and reset the LLM system prompts."""
from __future__ import annotations

import html
import logging
import time
from typing import Any


from apps.core.ops_bot.status import get_main_keyboard
from apps.core.ops_bot.telegram_api import register_subscriber, send_message

log = logging.getLogger("telegram_config_bot")


def send_long_prompt(chat_id: int | str, header_text: str, prompt_text: str, footer_text: str) -> None:
    """Sends header, chunked prompt text inside <pre>, and footer to avoid Telegram 4096 char limit."""
    send_message(chat_id, header_text)
    time.sleep(0.04)

    chunk_size = 2800
    clean_prompt = prompt_text or ""
    if len(clean_prompt) <= chunk_size:
        esc_chunk = html.escape(clean_prompt)
        send_message(chat_id, f"<pre>{esc_chunk}</pre>")
    else:
        total_parts = (len(clean_prompt) + chunk_size - 1) // chunk_size
        for idx in range(0, len(clean_prompt), chunk_size):
            part_num = (idx // chunk_size) + 1
            chunk = clean_prompt[idx:idx + chunk_size]
            esc_chunk = html.escape(chunk)
            part_msg = f"<b>بخش {part_num} از {total_parts}:</b>\n<pre>{esc_chunk}</pre>"
            send_message(chat_id, part_msg)
            time.sleep(0.05)

    send_message(chat_id, footer_text, reply_markup=get_main_keyboard())


def handle_prompts_list(chat_id: int | str) -> None:
    """Lists all system prompts grouped by category."""
    register_subscriber(chat_id)
    from apps.core.prompts import get_all_prompts_info, ensure_prompts_seeded

    try:
        ensure_prompts_seeded()
    except Exception as e:
        log.warning("ensure_prompts_seeded warning: %s", e)

    all_prompts = get_all_prompts_info()
    if not all_prompts:
        send_message(chat_id, "هیچ پرامپتی در کاتالوگ سامانه یافت نشد.", reply_markup=get_main_keyboard())
        return

    categories: dict[str, list[dict[str, Any]]] = {}
    for p in all_prompts:
        cat = p.get("category", "سایر بخش‌ها")
        categories.setdefault(cat, []).append(p)

    intro = (
        "<b>سامانه مدیریت و پایش پرامپت‌های لونا (gpt-6-luna)</b>\n\n"
        "در این بخش پرامپت‌های تمام ۱۹ نقطه فراخوانی مدل لونا در سراسر ماژول‌های سامانه در دسترس شما قرار دارد.\n"
        "هر پرامپت دارای کاربرد، ورودی، خروجی و ماژول مشخص است و در صورت تغییر توسط شما، به صورت آنی و بلادرنگ در تمام بخش‌های فعال سامانه اعمال خواهد شد.\n\n"
        "فهرست پرامپت‌ها بر اساس دسته‌بندی عملیاتی:\n"
    )
    send_message(chat_id, intro)
    time.sleep(0.04)

    counter = 1
    for cat_name, items in categories.items():
        lines = [f"<b>{html.escape(cat_name)}</b> (تعداد: {len(items)}):"]
        for item in items:
            key = item.get("key", "")
            title = item.get("title", key)
            is_custom = item.get("is_customized", False)
            status_txt = "شخصی‌سازی شده (فعال)" if is_custom else "پیش‌فرض سامانه"
            lines.append(
                f"{counter}. <b>{html.escape(title)}</b>\n"
                f"   کلید سیستمی: <code>{key}</code>\n"
                f"   ماژول: <code>{html.escape(item.get('module_path', ''))}</code>\n"
                f"   وضعیت: <b>{status_txt}</b>\n"
                f"   مشاهده و ویرایش: /prompt_{key}"
            )
            counter += 1
        msg = "\n\n".join(lines)
        send_message(chat_id, msg)
        time.sleep(0.05)

    footer = (
        "<b>راهنمای دستورات پرامپت:</b>\n"
        "• جهت مشاهده جزئیات، شرح کاربرد و متن کامل هر پرامپت دستور <code>/prompt_KEY</code> را ارسال نمایید.\n"
        "• جهت ویرایش تعاملی و ارسال متن جدید دستور <code>/edit_KEY</code> را ارسال فرمایید.\n"
        "• جهت بازگردانی به متن پیش‌فرض کارخانه دستور <code>/resetprompt_KEY</code> را استفاده کنید."
    )
    send_message(chat_id, footer, reply_markup=get_main_keyboard())


def handle_single_prompt(chat_id: int | str, key: str) -> None:
    """Shows complete details, purpose, caller module, status, and full text of a prompt."""
    register_subscriber(chat_id)
    from apps.core.prompts import get_prompt_info

    info = get_prompt_info(key)
    if not info:
        send_message(
            chat_id,
            f"پرامپت با کلید <code>{html.escape(key)}</code> در سامانه یافت نشد.\n\nجهت مشاهده فهرست کلیدهای معتبر از دستور /prompts استفاده نمایید.",
            reply_markup=get_main_keyboard()
        )
        return

    title = info.get("title", key)
    cat = info.get("category", "عمومی")
    module_path = info.get("module_path", "")
    desc = info.get("description", "")
    is_custom = info.get("is_customized", False)
    status_txt = "شخصی‌سازی شده (تغییر یافته توسط مدیر)" if is_custom else "پیش‌فرض اولیه سامانه"
    version = info.get("version", 1)

    time_str = ""
    updated_at = info.get("updated_at")
    if updated_at:
        try:
            from apps.core.jalali import format_jalali_datetime
            time_str = f" | آخرین به‌روزرسانی: {format_jalali_datetime(updated_at)}"
        except Exception:
            time_str = f" | آخرین به‌روزرسانی: {str(updated_at)[:16]}"

    header = (
        f"<b>مشخصات پرامپت: {html.escape(title)}</b>\n\n"
        f"کلید سیستمی: <code>{key}</code>\n"
        f"دسته‌بندی: <b>{html.escape(cat)}</b>\n"
        f"ماژول فراخواننده: <code>{html.escape(module_path)}</code>\n"
        f"وضعیت فعال: <b>{status_txt}</b>\n"
        f"نسخه: <b>{version}</b>{time_str}\n\n"
        f"<b>کاربرد و هدف این پرامپت در سامانه:</b>\n"
        f"<i>{html.escape(desc)}</i>\n\n"
        f"<b>متن کامل پرامپت ارسالی به مدل لونا (gpt-6-luna):</b>"
    )

    prompt_text = info.get("current_prompt", "")

    footer = (
        f"<b>اقدامات مدیریتی پرامپت «{html.escape(title)}»:</b>\n\n"
        f"• ویرایش و ثبت متن جدید: /edit_{key}\n"
        f"• بازگردانی به متن پیش‌فرض کارخانه: /resetprompt_{key}\n"
        f"• بازگشت به فهرست پرامپت‌ها: /prompts"
    )

    send_long_prompt(chat_id, header, prompt_text, footer)


def handle_edit_prompt_start(chat_id: int | str, key: str) -> None:
    """Begins interactive editing state for a prompt."""
    register_subscriber(chat_id)
    from apps.core.prompts import get_prompt_info

    info = get_prompt_info(key)
    if not info:
        send_message(chat_id, f"پرامپت با کلید <code>{html.escape(key)}</code> یافت نشد.", reply_markup=get_main_keyboard())
        return

    USER_STATES[chat_id] = {
        "action": "waiting_prompt_text",
        "key": key
    }

    title = info.get("title", key)
    msg = (
        f"<b>ویرایش پرامپت: {html.escape(title)}</b>\n"
        f"کلید سیستمی: <code>{key}</code>\n"
        f"ماژول: <code>{html.escape(info.get('module_path', ''))}</code>\n\n"
        f"لطفاً متن جدید پرامپت را در قالب یک پیام متنی ارسال فرمایید.\n\n"
        f"توجه مهم: به محض ارسال متن جدید، پرامپت بلافاصله در پایگاه داده و فایل اشتراکی ذخیره شده و تمامی ماژول‌های فعال سامانه از همان لحظه بدون نیاز به راه‌اندازی مجدد، با پرامپت جدید کار خواهند کرد.\n\n"
        f"جهت لغو و انصراف از ویرایش، دستور /cancel را ارسال نمایید."
    )
    send_message(chat_id, msg)


def handle_edit_prompt_save(chat_id: int | str, key: str, new_text: str) -> None:
    """Saves new prompt text and ensures instant propagation across all workers."""
    register_subscriber(chat_id)
    USER_STATES.pop(chat_id, None)
    from apps.core.prompts import set_system_prompt, get_prompt_info

    clean_text = (new_text or "").strip()
    if len(clean_text) < 10:
        send_message(
            chat_id,
            "متن پرامپت ارسال‌شده بسیار کوتاه یا فاقد محتوای معتبر است (حداقل ۱۰ کاراکتر مورد نیاز است). عملیات ذخیره لغو شد.",
            reply_markup=get_main_keyboard()
        )
        return

    ok, message = set_system_prompt(key, clean_text)
    info = get_prompt_info(key) or {}
    title = info.get("title", key)
    version = info.get("version", 1)

    if ok:
        success_msg = (
            f"<b>تغییر پرامپت با موفقیت ذخیره و اعمال شد.</b>\n\n"
            f"عنوان پرامپت: <b>{html.escape(title)}</b>\n"
            f"کلید سیستمی: <code>{key}</code>\n"
            f"نسخه جدید: <b>{version}</b>\n"
            f"تعداد کاراکتر: <b>{len(clean_text)}</b>\n\n"
            f"نتیجه عملیات: <i>{html.escape(message)}</i>\n\n"
            f"پرامپت جدید به صورت بلادرنگ در حافظه و لایه اشتراکی تمامی ورکرها قرار گرفت.\n\n"
            f"مشاهده پرامپت به‌روزشده: /prompt_{key}\n"
            f"بازگشت به فهرست پرامپت‌ها: /prompts"
        )
        send_message(chat_id, success_msg, reply_markup=get_main_keyboard())
    else:
        fail_msg = (
            f"خطا در به‌روزرسانی پرامپت <code>{html.escape(key)}</code>:\n"
            f"<code>{html.escape(message)}</code>"
        )
        send_message(chat_id, fail_msg, reply_markup=get_main_keyboard())


def handle_reset_prompt(chat_id: int | str, key: str) -> None:
    """Resets a prompt to its original system default text."""
    register_subscriber(chat_id)
    from apps.core.prompts import reset_system_prompt, get_prompt_info

    info = get_prompt_info(key)
    if not info:
        send_message(chat_id, f"پرامپت با کلید <code>{html.escape(key)}</code> یافت نشد.", reply_markup=get_main_keyboard())
        return

    title = info.get("title", key)
    ok, message = reset_system_prompt(key)

    msg = (
        f"<b>بازنشانی پرامپت: {html.escape(title)}</b>\n\n"
        f"کلید سیستمی: <code>{key}</code>\n"
        f"نتیجه عملیات: <i>{html.escape(message)}</i>\n\n"
        f"پرامپت با موفقیت به متن پیش‌فرض کارخانه بازگشت و تغییر به صورت آنی در تمام ماژول‌ها اعمال گردید.\n\n"
        f"مشاهده متن فعلی: /prompt_{key}\n"
        f"فهرست تمام پرامپت‌ها: /prompts"
    )
    send_message(chat_id, msg, reply_markup=get_main_keyboard())


# In-memory user state tracker for interactive multi-step commands (e.g. prompt editing)
USER_STATES: dict[int | str, dict[str, Any]] = {}
