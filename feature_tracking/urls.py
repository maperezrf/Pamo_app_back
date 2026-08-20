from django.urls import path

from . import views, webhooks

app_name = 'feature_tracking'

urlpatterns = [
    path('prototipos/', views.GovernancePrototipoListCreateAPI.as_view(), name='prototipo_list_create'),
    path('prototipos/admin/', views.GovernancePrototipoAdminListAPI.as_view(), name='prototipo_admin_list'),
    path('prototipos/<uuid:id>/', views.GovernancePrototipoDetailAPI.as_view(), name='prototipo_detail'),
    path('webhooks/github/merge/', webhooks.GitHubMergeWebhook.as_view(), name='webhook_github_merge'),
]
