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
        self.assertIn('مشتری‌های بالقوه‌ات رو', content)
        self.assertIn('پیدا کن', content)
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
