from django.test import TestCase, Client
from django.urls import reverse

class LandingPageTestCase(TestCase):
    def setUp(self):
        self.client = Client()

    def test_landing_page_status_and_template(self):
        response = self.client.get(reverse('core:landing'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pages/landing.html')
        self.assertTemplateUsed(response, 'base.html')

    def test_persian_first_and_rtl_attributes(self):
        response = self.client.get(reverse('core:landing'))
        content = response.content.decode('utf-8')
        self.assertIn('dir="rtl"', content)
        self.assertIn('lang="fa"', content)
        self.assertIn('Vazirmatn', content)

    def test_landing_core_sections_presence(self):
        response = self.client.get(reverse('core:landing'))
        content = response.content.decode('utf-8')
        # Core brand and headlines
        self.assertIn('مشتری‌یاب', content)
        self.assertIn('به خریدار تبدیل کن', content)
        self.assertIn('پیدا', content)
        # Circular step cards
        self.assertIn('چند قدم تا پیدا کردن خریداران واقعی', content)
        self.assertIn('معرفی محصول', content)
        self.assertIn('اسکن گفتگوها', content)
        self.assertIn('سنجش نیت خرید', content)
        self.assertIn('پیشنهاد ارتباط', content)
        # Opportunity preview
        self.assertIn('نیاز را می‌بیند', content)
        self.assertIn('۹۴ / ۱۰۰', content)
        # Sources
        self.assertIn('ایکس (توییتر)', content)
        self.assertIn('تلگرام', content)
        self.assertIn('بات سراسری پیدا (@peyda_bot)', content)
        self.assertIn('دیوار', content)
        # Theme toggle
        self.assertIn('data-theme-toggle', content)


class SiteAppearanceTests(TestCase):
    def test_default_and_uploaded_background(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.core.models import SiteAppearance

        html = self.client.get(reverse('core:landing')).content.decode()
        self.assertIn('--ui-overlay: 0.2', html)
        self.assertNotIn('--ui-bg:', html)            # bundled photo is used
        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
               b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")
        row = SiteAppearance(overlay_percent=45)
        row.background_image.save("bg.png", SimpleUploadedFile("bg.png", png, "image/png"), save=False)
        row.save()
        html = self.client.get(reverse('core:landing')).content.decode()
        self.assertIn('--ui-overlay: 0.45', html)
        self.assertIn('--ui-bg: url("/media/site/', html)
        row.background_image.delete(save=False)
