import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)

def send_verification_email(email: str, code: str, first_name: str = ""):
    """
    Sends a styled, responsive Persian HTML verification email with the 6-digit OTP code.
    """
    subject = f"کد احراز هویت شما در مشتری‌یاب: {code}"
    from_email = settings.DEFAULT_FROM_EMAIL or "sitebartar3@gmail.com"
    recipient_list = [email]

    user_greeting = f"سلام {first_name} گرامی" if first_name else "سلام کاربر گرامی"

    # Plain text version for fallback
    text_content = f"""{user_greeting}،
کد احراز هویت شما در سامانه مشتری‌یاب:
{code}

این کد به مدت ۱۰ دقیقه معتبر است.
اگر شما این درخواست را ارسال نکرده‌اید، این پیام را نادیده بگیرید.
مشتری‌یاب - پلتفرم هوشمند کشف مشتری در گفتگوهای آنلاین
"""

    # High quality HTML email version
    html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>کد احراز هویت مشتری‌یاب</title>
</head>
<body style="margin:0; padding:0; background-color:#f4f5fa; font-family:Tahoma, 'Segoe UI', Arial, sans-serif; direction:rtl; text-align:right;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f4f5fa; padding:30px 10px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width:560px; background-color:#ffffff; border-radius:18px; overflow:hidden; box-shadow:0 12px 35px rgba(0,0,0,0.08); border:1px solid #e2e8f0;">
          
          <!-- Header Banner -->
          <tr>
            <td style="background:linear-gradient(135deg, #0e0e24 0%, #26114a 50%, #151336 100%); padding:36px 30px; text-align:center;">
              <div style="display:inline-block; width:48px; height:48px; line-height:48px; border-radius:14px; background:linear-gradient(135deg, #e8268e, #7928ca); color:#ffffff; font-size:24px; font-weight:bold; margin-bottom:12px;">
                م
              </div>
              <h1 style="margin:0; font-size:24px; color:#ffffff; font-weight:800; letter-spacing:-0.5px;">مشتری‌یاب</h1>
              <p style="margin:6px 0 0; font-size:12px; color:#cbd5e1;">دستیار هوشمند کشف مشتری در گفتگوهای آنلاین</p>
            </td>
          </tr>

          <!-- Body Content -->
          <tr>
            <td style="padding:36px 32px;">
              <h2 style="margin:0 0 16px; font-size:18px; color:#0f172a; font-weight:700;">{user_greeting}،</h2>
              <p style="margin:0 0 24px; font-size:14px; line-height:1.8; color:#475569;">
                از این‌که به جمع کاربران <strong>مشتری‌یاب</strong> پیوستید خرسندیم. برای تکمیل فرآیند احراز هویت و فعال‌سازی حساب کسب‌وکار خود، لطفاً از کد تأیید زیر استفاده نمایید:
              </p>

              <!-- OTP Code Display Card -->
              <div style="background-color:#f8f9fe; border:2px dashed #7928ca; border-radius:14px; padding:24px; text-align:center; margin:28px 0;">
                <span style="display:block; font-size:12px; color:#64748b; margin-bottom:8px; font-weight:600;">کد تأیید ۶ رقمی شما:</span>
                <span style="display:inline-block; font-size:38px; font-weight:900; color:#7928ca; letter-spacing:10px; font-family:'Courier New', Courier, monospace; direction:ltr;">
                  {code}
                </span>
                <span style="display:block; font-size:11px; color:#94a3b8; margin-top:8px;">معتبر به مدت ۱۰ دقیقه</span>
              </div>

              <!-- Security Notice -->
              <div style="background-color:#fffbeb; border-right:4px solid #f59e0b; border-radius:6px; padding:12px 16px; margin-bottom:24px;">
                <p style="margin:0; font-size:12px; line-height:1.7; color:#92400e;">
                  <strong>نکته امنیتی:</strong> این کد کاملاً محرمانه است. لطفاً آن را تحت هیچ شرایطی در اختیار افراد دیگر قرار ندهید.
                </p>
              </div>

              <p style="margin:0; font-size:13px; color:#64748b; line-height:1.8;">
                اگر شما درخواست ساخت حساب در سامانه مشتری‌یاب را ثبت نکرده‌اید، می‌توانید با خیال راحت این ایمیل را نادیده بگیرید.
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color:#f8fafc; border-top:1px solid #e2e8f0; padding:20px 32px; text-align:center;">
              <p style="margin:0; font-size:11px; color:#94a3b8; line-height:1.6;">
                این ایمیل به صورت خودکار توسط سامانه هوشمند «مشتری‌یاب» ارسال شده است.<br>
                © ۱۴۰۵ تمامی حقوق محفوظ است.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    msg = EmailMultiAlternatives(subject, text_content, from_email, recipient_list)
    msg.attach_alternative(html_content, "text/html")
    try:
        msg.send(fail_silently=False)
        logger.info(f"Verification email sent successfully to {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send verification email to {email}: {e}")
        raise e
