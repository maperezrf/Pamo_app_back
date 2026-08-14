from django.conf import settings
from django.db import models


class AllowedEmail(models.Model):
    """Gate de entrada: solo estos correos pueden iniciar sesión con Google.
    No implica ningún permiso una vez adentro -- eso lo maneja Group (ver
    accounts/permissions.py)."""

    email = models.EmailField(unique=True)
    notes = models.CharField(max_length=200, blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['email']

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email
