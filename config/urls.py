from django.contrib import admin
from django.urls import include, path

from .health import health

urlpatterns = [
    path('health/', health, name='health'),
    path('admin/', admin.site.urls),
    path('api/auth/', include('accounts.urls')),
    path('api/tracking/', include('feature_tracking.urls')),
    path('api/facturacion/', include('facturacion.urls')),
    path('api/catalogo/', include('catalogo.urls')),
    path('api/pedidos/', include('pedidos.urls')),
    path('api/communications/', include('communications.urls')),
]
