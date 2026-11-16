from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.businesses.models import Business
from apps.products.models import Category, Product
from apps.products.taxonomy import organize_products


class CanonicalOrganizerTests(TestCase):
    def test_product_organizer_cannot_create_categories_from_model_response(self):
        user = get_user_model().objects.create_user(username='canonical', password=None)
        business = Business.objects.create(user=user, name='پوشاک', business_type='PHYSICAL')
        category = Category.objects.create(business=business, name='شومیز زنانه')
        products = [Product.objects.create(business=business, name=f'شومیز {i}', description='x') for i in range(20)]
        before = Category.objects.count()
        def llm(*args, **kwargs):
            return {'assignments': [
                {'product_id': p.pk, 'category_id': None,
                 'new_category': {'parent_id': category.pk, 'name': f'دسته تازه {i}'}}
                for i, p in enumerate(products)
            ]}
        result = organize_products(business, llm=llm)
        self.assertEqual(Category.objects.count(), before)
        self.assertEqual(result['created'], 0)
        self.assertFalse(Product.objects.filter(business=business, category__isnull=False).exists())

    def test_valid_existing_category_is_reused_without_new_nodes(self):
        user = get_user_model().objects.create_user(username='existing', password=None)
        business = Business.objects.create(user=user, name='پوشاک', business_type='PHYSICAL')
        category = Category.objects.create(business=business, name='شومیز زنانه')
        products = [Product.objects.create(business=business, name=f'شومیز {i}', description='x') for i in range(10)]
        def llm(*args, **kwargs):
            return {'assignments': [{'product_id': p.pk, 'category_id': category.pk} for p in products]}
        result = organize_products(business, llm=llm)
        self.assertEqual(result['placed'], 10)
        self.assertEqual(result['created'], 0)
        self.assertEqual(set(Product.objects.filter(business=business).values_list('category_id', flat=True)), {category.pk})
