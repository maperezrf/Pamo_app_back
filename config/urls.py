from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/tracking/', include('feature_tracking.urls')),
    path('api/facturacion/', include('facturacion.urls')),
]
