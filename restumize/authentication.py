import base64
import hmac
import time
import uuid

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import ugettext as _

from restumize.http import HttpUnauthorized

try:
    from hashlib import sha1
except ImportError:
    import sha
    sha1 = sha.sha

try:
    import python_digest
except ImportError:
    python_digest = None

try:
    import oauth2
except ImportError:
    oauth2 = None

try:
    import oauth_provider
except ImportError:
    oauth_provider = None


class Authentication(object):
    """
    A simple base class to establish the protocol for auth.

    By default, this indicates the user is always authenticated.
    """
    def __init__(self, require_active=True):
        self.require_active = require_active

    def is_authenticated(self, request, **kwargs):
        """
        Identifies if the user is authenticated to continue or not.

        Should return either ``True`` if allowed, ``False`` if not or an
        ``HttpResponse`` if you need something custom.
        """
        return True

    def get_identifier(self, request):
        """
        Provides a unique string identifier for the requestor.

        This implementation returns a combination of IP address and hostname.
        """
        return "%s_%s" % (request.META.get('REMOTE_ADDR', 'noaddr'), request.META.get('REMOTE_HOST', 'nohost'))

    def check_active(self, user):
        """
        Ensures the user has an active account.

        Optimized for the ``django.contrib.auth.models.User`` case.
        """
        if not self.require_active:
            # Ignore & move on.
            return True

        return user.is_active


class TokenAuthentication(Authentication):
    """
    Handles API key auth, in which a user provides an API key/token.
    """
    def _unauthorized(self):
        return HttpUnauthorized()

    def extract_credentials(self, request):
        return request.GET.get('token') or request.POST.get('token')

    def is_authenticated(self, request, **kwargs):
        """
        Finds the user and checks their API key.

        Should return either ``True`` if allowed, ``False`` if not or an
        ``HttpResponse`` if you need something custom.
        """

        try:
            token = self.extract_credentials(request)
        except ValueError:
            return self._unauthorized()

        if not token:
            return self._unauthorized()

        if self.get_key(token):
            user = self.get_user(token)

            if not self.check_active(user):
                return False

            request.user = user
            return True

        return False

    def get_key(self, token):
        """
        Attempts to find the API key for the user. Uses ``Token`` by default
        but can be overridden.
        """
        from restumize.models import Token

        try:
            Token.objects.get(token=token)
        except Token.DoesNotExist:
            return False

        return True

    def get_user(self, token):
        from restumize.models import Token
        token = Token.objects.get(token=token)
        return token.user

    def get_identifier(self, request):
        """
        Provides a unique string identifier for the requestor.

        This implementation returns the api token.
        """
        address = request.META.get('REMOTE_ADDR', 'noaddr')
        host = request.META.get('REMOTE_HOST', 'nohost')
        token = self.extract_credentials(request) or 'notoken'
        return "%s_%s_%s" % (address, host, token)


class CookieAuthentication(Authentication):
    """
    Handles authentication from already logged-in user. User must log in via django apps.
    """

    def is_authenticated(self, request, **kwargs):
        "Return True if user is not anonymous."
        if request.user.is_authenticated():
            return True
        else:
            return False


class AnonymousAuthentication(Authentication):
    """
    Handles anonymous authentication.
    """

    def is_authenticated(self, request, **kwargs):
        "Return True for everyone."
        return True
