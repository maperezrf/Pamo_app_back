from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('csrf/', views.csrf, name='csrf'),
    path('google/', views.GoogleLoginAPI.as_view(), name='google_login'),
    path('me/', views.MeAPI.as_view(), name='me'),
    path('logout/', views.LogoutAPI.as_view(), name='logout'),
    path('ping-admin/', views.PingAdminAPI.as_view(), name='ping_admin'),
]
