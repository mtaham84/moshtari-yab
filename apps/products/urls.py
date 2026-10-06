from django.urls import path
from . import views

app_name = "products"

urlpatterns = [
    path("", views.product_list_view, name="list"),
    path("add/", views.product_add_view, name="add"),
    path("<int:pk>/", views.product_detail_view, name="detail"),
    path("<int:pk>/card/", views.public_product_card_view, name="public_card"),
    path("<int:pk>/edit/", views.product_edit_view, name="edit"),
    path("<int:pk>/delete/", views.product_delete_view, name="delete"),
    path("<int:pk>/images/<int:image_pk>/set-main/", views.set_main_image_view, name="set_main_image"),
    path("<int:pk>/images/<int:image_pk>/delete/", views.delete_image_view, name="delete_image"),
    path("api/suggest-category/", views.api_suggest_category, name="api_suggest_category"),
    path("api/category-attributes/<int:category_id>/", views.api_category_attributes, name="api_category_attributes"),
    path("categories/api/add/", views.api_create_category, name="api_create_category"),
    path("categories/api/tree/", views.api_category_tree, name="api_category_tree"),
]
