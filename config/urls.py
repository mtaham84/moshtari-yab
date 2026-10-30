from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve


from apps.products.views import product_click_view, public_product_card_view
from apps.accounts.views import login_view
from apps.accounts.shop import shop_view
 
urlpatterns = [
    path("manage/", include("apps.billing.manage_urls")),
    path("admin/", admin.site.urls),
    path("ops/", include("apps.billing.urls")),
    path("shop/<slug:slug>/", shop_view, name="public_shop"),
    path("p/<int:pk>/", public_product_card_view, name="short_public_card"),
    path("r/<int:pk>/", product_click_view, name="product_click"),
    path("login/", login_view, name="login_alias"),
    path("accounts/", include("apps.accounts.urls")),
    path("products/", include("apps.products.urls")),
    path("discovery/", include("apps.discovery.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]

