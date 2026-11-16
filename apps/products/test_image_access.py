"""Image actions must retain tenant boundaries and never redirect off site."""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.businesses.models import Business
from apps.products.models import Product, ProductImage


@pytest.fixture
def image_setup(db, client):
    user = get_user_model().objects.create_user(username='image-owner@example.com')
    business = Business.objects.create(user=user, name='Owner', onboarding_done=True)
    product = Product.objects.create(business=business, name='Product')
    image = ProductImage.objects.create(product=product, image='products/image.jpg')
    client.force_login(user)
    return client, product, image


@pytest.mark.parametrize('action', ['set_main_image', 'delete_image'])
@pytest.mark.parametrize('referer', [
    'https://attacker.example/edit',
    '//attacker.example/detail',
    'http://testserver.attacker.example/edit',
])
def test_image_action_rejects_external_redirect(image_setup, action, referer):
    client, product, image = image_setup
    response = client.post(reverse('products:' + action, args=[product.pk, image.pk]), HTTP_REFERER=referer)
    assert response.status_code == 302
    assert response['Location'] == reverse('products:edit', args=[product.pk])


@pytest.mark.parametrize('action', ['set_main_image', 'delete_image'])
def test_image_action_preserves_local_return(image_setup, action):
    client, product, image = image_setup
    target = reverse('products:detail', args=[product.pk])
    response = client.post(reverse('products:' + action, args=[product.pk, image.pk]), HTTP_REFERER=target)
    assert response['Location'] == target


@pytest.mark.parametrize('action', ['set_main_image', 'delete_image'])
def test_other_seller_cannot_change_image(image_setup, action):
    client, product, image = image_setup
    user = get_user_model().objects.create_user(username='image-other@example.com')
    Business.objects.create(user=user, name='Other', onboarding_done=True)
    client.force_login(user)
    response = client.post(reverse('products:' + action, args=[product.pk, image.pk]))
    assert response.status_code == 404
    image.refresh_from_db()
    assert image.product_id == product.pk
    assert image.is_main is False
