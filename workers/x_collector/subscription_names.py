"""Required product-name spellings for AI subscription offerings, shared by all sellers."""
from need_engine.text import norm

GPT_NAMES = ('اشتراک GPT', 'اشتراک ChatGPT', 'اشتراک جی پی تی')
CLAUDE_NAMES = ('اشتراک کلاد', 'اشتراک Claude')


def subscription_names(product):
    text = norm(' '.join(str(getattr(product, key, '') or '') for key in
                        ('title', 'description', 'category_path')) + ' ' +
                ' '.join(getattr(product, 'titles', []) or []))
    if not any(word in text for word in ('اشتراک', 'اکانت', 'subscription')):
        return []
    gpt = any(word in text for word in ('gpt', 'جی پی تی', 'جیپی', 'چت جی'))
    claude = any(word in text for word in ('claude', 'کلاد', 'کلاود'))
    generic = not (gpt or claude) and 'هوش مصنوعی' in text
    return [*(GPT_NAMES if gpt or generic else ()), *(CLAUDE_NAMES if claude or generic else ())]
