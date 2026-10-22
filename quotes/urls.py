from django.urls import path

from .apis import QuoteListAPI, QuotePdfAPI, QuotePdfLinkAPI

urlpatterns = [
    path("", QuoteListAPI.as_view(), name="quote-list"),
    path("<int:draft_order_id>/pdf/", QuotePdfAPI.as_view(), name="quote-pdf"),
    path("pdf/<str:token>/", QuotePdfLinkAPI.as_view(), name="quote-pdf-link"),
]
