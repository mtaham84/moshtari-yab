"""Stable X dedup keys; unscoped legacy hashes keep their original value."""
import hashlib


def compute_tweet_hash(tweet_id: str | int, product_id: str | int | None = None) -> str:
    key = f"x_tweet:{str(tweet_id).strip()}"
    if product_id is not None and str(product_id):
        key += f"|product:{product_id}"
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def seen_tweet_ids(keys: set[str], product_ids: list[str]) -> set[str]:
    """Only suppress a tweet when every product in this search has already seen it."""
    if not product_ids:
        return {key for key in keys if '|' not in key}
    by_product = [{key.split('|', 1)[0] for key in keys if key.endswith(f'|{pid}')} for pid in product_ids]
    return set.intersection(*by_product)
