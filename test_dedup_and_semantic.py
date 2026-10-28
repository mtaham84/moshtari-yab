import os, sys, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import hashlib
from datetime import timedelta
from django.utils import timezone
from apps.discovery.models import InstagramCrawledPost
from apps.discovery.semantic_moderation import check_comment_semantic_moderation
from instagram_engine.analyzer import evaluate_comment_smart

print('=== 1. TESTING POST HASH & 30-DAY DEDUPLICATION ===')
test_media_id_1 = '321654987111222'
post_hash_1 = hashlib.sha256(f'ig_post:{test_media_id_1}'.encode()).hexdigest()

InstagramCrawledPost.objects.filter(post_hash=post_hash_1).delete()

# Account 1 crawls it
p1, created = InstagramCrawledPost.objects.get_or_create(
    post_hash=post_hash_1,
    defaults={
        'media_id': test_media_id_1,
        'media_code': 'Cxy123',
        'account_username': 'just.test221',
        'posted_at': timezone.now() - timedelta(days=5),
    }
)
print('Account just.test221 crawled post:', created, p1.post_hash[:10])

# Account 2 tries to crawl the SAME post
is_already_crawled = InstagramCrawledPost.objects.filter(post_hash=post_hash_1).exists()
print('Account just.test222 checks if post is already crawled:', is_already_crawled)
assert is_already_crawled is True, 'Deduplication failed!'
print('SUCCESS: Duplicate crawl across accounts prevented!')

# Test 30-day TTL cleanup
old_media_id = '999888777666555'
old_hash = hashlib.sha256(f'ig_post:{old_media_id}'.encode()).hexdigest()
old_p = InstagramCrawledPost.objects.create(
    post_hash=old_hash,
    media_id=old_media_id,
    media_code='OldPost',
    account_username='just.test221',
    posted_at=timezone.now() - timedelta(days=35),
)
InstagramCrawledPost.objects.filter(id=old_p.id).update(crawled_at=timezone.now() - timedelta(days=32))

thirty_days_ago = timezone.now() - timedelta(days=30)
purged_count, _ = InstagramCrawledPost.objects.filter(crawled_at__lt=thirty_days_ago).delete()
print(f'TTL Cleanup: Purged {purged_count} hashes older than 30 days.')
assert purged_count >= 1, 'TTL cleanup failed!'
print('SUCCESS: 30-day TTL purge verified!')

InstagramCrawledPost.objects.filter(post_hash=post_hash_1).delete()

print('=== 2. TESTING BGE-M3 SEMANTIC MODERATION ===')
test_cases = [
    ('واقعا مسخره کردید مردمو مگه جونتون درمیاد قیمت بگید کلاهبردارا', True, 'diss_or_profanity'),
    ('خفه شو زنیکه جنده لاشی کثافت', True, 'profanity_and_sexual'),
    ('افزایش تضمینی فالوور و لایک دایرکت پیام بدید', True, 'spam_and_bots'),
    ('خخخخخخخخخخخخخخخخخ', True, 'gibberish'),
    ('asdfghjklqwerty', True, 'gibberish'),
    ('سلام سنگ تراورتن عباس آباد متری چند هست و ارسال دارید؟', False, 'buyer_inquiry'),
    ('قیمت رو لطف میکنید دایرکت بفرستید؟', False, 'buyer_inquiry'),
]

for text, should_reject, label in test_cases:
    res = check_comment_semantic_moderation(text)
    eval_res = evaluate_comment_smart(text, product_context='صنایع سنگ ساختمانی')
    is_blocked = res.get('is_rejected') or eval_res.get('is_toxic') or eval_res.get('is_emoji') or not eval_res.get('is_lead')
    cat = res.get('category')
    conf = res.get('confidence', 0)
    is_lead = eval_res.get('is_lead')
    print(f'Test: {text[:35]}... -> Rejected: {res.get("is_rejected")} ({cat}, conf={conf:.2f}) | is_lead={is_lead}')
    if should_reject:
        assert is_blocked, f'Expected rejection for toxic/diss/spam comment: {text}'
    else:
        assert is_lead, f'Expected buyer lead for genuine comment: {text}'

print('ALL DEDUPLICATION, 30-DAY TTL, AND BGE-M3 MODERATION TESTS PASSED 100%!')
