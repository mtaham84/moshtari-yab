from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve


from apps.products.views import product_click_view, public_product_card_view
 
urlpatterns = [
    path("admin/", admin.site.urls),
    path("p/<int:pk>/", public_product_card_view, name="short_public_card"),
    path("r/<int:pk>/", product_click_view, name="product_click"),
    path("accounts/", include("apps.accounts.urls")),
    path("products/", include("apps.products.urls")),
    path("discovery/", include("apps.discovery.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]

