from django.conf import settings
from django.contrib import admin


if 'django.contrib.auth' in settings.INSTALLED_APPS:
    from restumize.models import Token

    class TokenInline(admin.StackedInline):
        model = Token
        extra = 0

    # Also.
    admin.site.register(Token)
