import pytest

from shared.x_filters import is_promotional_post
from workers.x_collector.worker import XCollector


@pytest.mark.parametrize('text', [
    'برای خرید لپ‌تاپ کمک می‌خوام', 'تخفیف داره؟', 'کسی برای خرید گوشی راهنمایی میکنه؟',
    'دنبال تخفیف برای ویلچر هستم', 'خرید این مدل خوبه؟ https://example.com/item',
    'برای خرید این کالا بودجه ندارم', 'کجا تخفیف واقعی پیدا کنم؟',
    'این فروشگاه برای خرید امنه؟ https://example.com', 'کد تخفیف کسی داره؟',
    'دوستان برای خرید لپتاپ این لینک رو بررسی میکنید https://example.com',
])
def test_buyer_sentences_are_not_promotional(text):
    assert not is_promotional_post(text)


@pytest.mark.parametrize('text', [
    'فروش ویژه لپتاپ امروز', 'خرید کنید و جایزه بگیرید', 'همین حالا بخرید',
    'ارسال رایگان همه سفارشات', 'فروش فوری تجهیزات', 'سفارش در دایرکت',
    'ارسال به سراسر کشور', 'ثبت سفارش از طریق واتساپ', 'سفارش دهید تا فردا تحویل بگیرید',
    'Shop now https://example.com',
])
def test_explicit_advertisements_are_promotional(text):
    assert is_promotional_post(text)


def test_worker_counts_dropped_promotional_posts(tmp_path):
    collector = XCollector(output_dir=tmp_path / 'out', state_path=tmp_path / 'state')
    result = collector.run(['query'], mock_records=[
        {'id': '1', 'author_id': 'seller', 'username': 'seller', 'text': 'فروش ویژه لپتاپ'},
    ])
    assert result['collected'] == 0
    assert result['promotional_posts_dropped'] == 1
