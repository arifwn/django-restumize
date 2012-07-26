
import warnings

from django.conf import settings
from django.conf.urls.defaults import *
from django.core.exceptions import ImproperlyConfigured
from django.core.urlresolvers import reverse
from django.http import HttpResponse
from django.utils.cache import patch_cache_control
from django.views.decorators.csrf import csrf_exempt

from restumize.exceptions import NotRegistered, BadRequest
from restumize.serializers import Serializer


class Api(object):
    """
    Implements a registry to tie together the various resources that make up
    an API.

    Especially useful for navigation, HATEOAS and for providing multiple
    versions of your API.

    Optionally supplying ``api_name`` allows you to name the API. Generally,
    this is done with version numbers (i.e. ``v1``, ``v2``, etc.) but can
    be named any string.
    """
    def __init__(self, api_name="v1"):
        self.api_name = api_name
        self._registry = {}
        self._canonicals = {}

    def register(self, resource_class, canonical=True):
        resource_name = getattr(resource_class._meta, 'resource_name', None)

        if resource_name is None:
            raise ImproperlyConfigured("Resource %r must define a 'resource_name'." % resource)

        self._registry[resource_name] = resource_class

        if canonical is True:
            if resource_name in self._canonicals:
                warnings.warn("A new resource '%r' is replacing the existing canonical URL for '%s'." % (resource, resource_name), Warning, stacklevel=2)

            self._canonicals[resource_name] = resource_class

    def wrap_view(self, resource_class, view='view'):
        """
        Wraps methods so they can be called in a more functional way as well
        as handling exceptions better.
        """
        @csrf_exempt
        def wrapper(request, *args, **kwargs):
            resource = resource_class(request.GET, request.POST, request.FILES)
            try:
                callback = getattr(resource, view)
                response = callback(request, *args, **kwargs)

                if request.is_ajax() and not response.has_header("Cache-Control"):
                    # IE excessively caches XMLHttpRequests, so we're disabling
                    # the browser cache here.
                    # See http://www.enhanceie.com/ie/bugs.asp for details.
                    patch_cache_control(response, no_cache=True)
                
                return response
            # except (BadRequest, fields.ApiFieldError), e:
            #     return http.HttpBadRequest(e.args[0])
            # except ValidationError, e:
            #     return http.HttpBadRequest(', '.join(e.messages))
            except Exception, e:
                if hasattr(e, 'response'):
                    return e.response

                # A real, non-expected exception.
                # Handle the case where the full traceback is more helpful
                # than the serialized error.
                if settings.DEBUG and getattr(settings, 'RESTO_FULL_DEBUG', False):
                    raise

                # Re-raise the error to get a proper traceback when the error
                # happend during a test case
                if request.META.get('SERVER_NAME') == 'testserver':
                    raise

                # Rather than re-raising, we're going to things similar to
                # what Django does. The difference is returning a serialized
                # error message.
                return resource._handle_500(request, e)

        return wrapper

    def resource_url(self, name):
        resource_class = self._registry[name]

        urlpatterns = patterns('',
            url(r"^(?P<resource_name>%s)%s$" % (resource_class._meta.resource_name, trailing_slash()), self.wrap_view(resource_class), name="api_view"),
        )

        return urlpatterns

    def prepend_urls(self):
        """
        A hook for adding your own URLs or matching before the default URLs.
        """
        return []

    @property
    def urls(self):
        """
        Provides URLconf details for the ``Api`` and all registered
        ``Resources`` beneath it.
        """
        pattern_list = []

        for name in sorted(self._registry.keys()):
            self._registry[name].api_name = self.api_name
            pattern_list.append((r"^(?P<api_name>%s)/" % self.api_name, include(self.resource_url(name))))

        urlpatterns = self.prepend_urls()

        urlpatterns += patterns('',
            *pattern_list
        )
        return urlpatterns


def trailing_slash():
    if getattr(settings, 'RESTUMIZE_ALLOW_MISSING_SLASH', False):
        return '/?'
    
    return '/'
