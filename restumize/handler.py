
import copy
import datetime
import logging
import mimeparse

import django
from django.conf import settings
from django.conf.urls.defaults import patterns, url
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned, ValidationError
from django.forms.util import ErrorDict, ErrorList
from django.http import HttpResponse, HttpResponseNotFound, Http404
from django.utils.datastructures import SortedDict
from django.views.decorators.csrf import csrf_exempt

from restumize.fields import BaseField, FileField
from restumize.serializers import Serializer
from restumize.authentication import Authentication
from restumize.authorization import ReadOnlyAuthorization
from restumize.cache import NoCache
from restumize.throttle import BaseThrottle
from restumize.exceptions import NotFound, BadRequest, ImmediateHttpResponse
from restumize import http

def get_declared_fields(bases, attrs, with_base_fields=True):
    fields = [(field_name, attrs.pop(field_name)) for field_name, obj in attrs.items() if isinstance(obj, BaseField)]

    # If this class is subclassing another Handler, add that Handler's fields.
    # Note that we loop over the bases in *reverse*. This is necessary in
    # order to preserve the correct order of fields.
    if with_base_fields:
        for base in bases[::-1]:
            if hasattr(base, 'base_fields'):
                fields = base.base_fields.items() + fields
    else:
        for base in bases[::-1]:
            if hasattr(base, 'declared_fields'):
                fields = base.declared_fields.items() + fields

    return SortedDict(fields)


class ResourceOptions(object):
    """
    A configuration class for ``Resource``.

    Provides sane defaults and the logic needed to augment these settings with
    the internal ``class Meta`` used on ``Resource`` subclasses.
    """
    serializer = Serializer()
    authentication = Authentication()
    authorization = ReadOnlyAuthorization()
    cache = NoCache()
    throttle = BaseThrottle()
    allowed_methods = ['get', 'post', 'put', 'delete', 'patch']
    limit = getattr(settings, 'API_LIMIT_PER_PAGE', 20)
    max_limit = 1000
    api_name = None
    resource_name = None
    urlconf_namespace = None
    default_format = 'application/json'

    def __new__(cls, meta=None):
        overrides = {}

        # Handle overrides.
        if meta:
            for override_name in dir(meta):
                # No internals please.
                if not override_name.startswith('_'):
                    overrides[override_name] = getattr(meta, override_name)

        allowed_methods = overrides.get('allowed_methods', ['get', 'post', 'put', 'delete', 'patch'])

        if overrides.get('list_allowed_methods', None) is None:
            overrides['list_allowed_methods'] = allowed_methods

        if overrides.get('detail_allowed_methods', None) is None:
            overrides['detail_allowed_methods'] = allowed_methods

        return object.__new__(type('ResourceOptions', (cls,), overrides))


class DeclarativeFieldsMetaclass(type):
    """
    Metaclass that converts Field attributes to a dictionary called
    'base_fields', taking into account parent class 'base_fields' as well.
    """
    def __new__(cls, name, bases, attrs):
        attrs['base_fields'] = get_declared_fields(bases, attrs)
        new_class = super(DeclarativeFieldsMetaclass,
                     cls).__new__(cls, name, bases, attrs)
        opts = getattr(new_class, 'Meta', None)
        new_class._meta = ResourceOptions(opts)

        if not getattr(new_class._meta, 'resource_name', None):
            # No ``resource_name`` provided. Attempt to auto-name the resource.
            class_name = new_class.__name__
            name_bits = [bit for bit in class_name.split('Resource') if bit]
            resource_name = ''.join(name_bits).lower()
            new_class._meta.resource_name = resource_name

        return new_class


class BaseHandler(object):

    __metaclass__ = DeclarativeFieldsMetaclass

    def __init__(self, get_data=None, post_data=None, files=None, error_class=list):
        self.error_class = error_class
        self.fields = copy.deepcopy(self.base_fields)
        self.get_data = get_data or {}
        self.post_data = post_data or {}
        self.files = files or {}

    def _get_raw_value(self, name):
        value = self.get_data.get(name)
        value = self.post_data.get(name, value)
        value = self.files.get(name, value)

        return value

    def _is_valid(self):
        self._full_clean()
        return not bool(self._errors)

    def _get_error_list(self):
        error_list = []
        for name, value in self._errors.iteritems():
            error_list.append((name,value))

        return error_list

    def _full_clean(self):
        self._cleaned_data = {}
        self._errors = {}
        self._clean_fields()
        self._replace_fields()
        if self._errors:
            del self._cleaned_data

    def _clean_fields(self):
        for name, field in self.fields.items():
            value = self._get_raw_value(name)
            try:
                value = field.clean(value)
                self._cleaned_data[name] = value
                if hasattr(self, 'clean_%s' % name):
                    value = getattr(self, 'clean_%s' % name)()
                    self._cleaned_data[name] = value
            except ValidationError, e:
                self._errors[name] = self.error_class(e.messages)
                if name in self._cleaned_data:
                    del self._cleaned_data[name]
    
    def _replace_fields(self):
        """
        Replace original fields with cleaned values.
        """
        for name, value in self._cleaned_data.items():
            setattr(self, name, value)

    def _handle_500(self, request, exception):
        import traceback
        import sys

        the_trace = '\n'.join(traceback.format_exception(*(sys.exc_info())))
        response_class = http.HttpApplicationError

        NOT_FOUND_EXCEPTIONS = (NotFound, ObjectDoesNotExist, Http404)

        if isinstance(exception, NOT_FOUND_EXCEPTIONS):
            response_class = HttpResponseNotFound

        if settings.DEBUG:
            data = {
                "error": unicode(exception),
                "traceback": the_trace,
            }
            desired_format = self._determine_format(request)
            serialized = self._serialize(request, data, desired_format)
            return response_class(content=serialized, content_type=build_content_type(desired_format))

        # When DEBUG is False, send an error message to the admins (unless it's
        # a 404, in which case we check the setting).
        if not isinstance(exception, NOT_FOUND_EXCEPTIONS):
            log = logging.getLogger('django.request.restumize')
            log.error('Internal Server Error: %s' % request.path, exc_info=sys.exc_info(), extra={'status_code': 500, 'request':request})

            if django.VERSION < (1, 3, 0) and getattr(settings, 'SEND_BROKEN_LINK_EMAILS', False):
                from django.core.mail import mail_admins
                subject = 'Error (%s IP): %s' % ((request.META.get('REMOTE_ADDR') in settings.INTERNAL_IPS and 'internal' or 'EXTERNAL'), request.path)
                try:
                    request_repr = repr(request)
                except:
                    request_repr = "Request repr() unavailable"

                message = "%s\n\n%s" % (the_trace, request_repr)
                mail_admins(subject, message, fail_silently=True)

        # Prep the data going out.
        data = {
            "error": getattr(settings, 'RESTUMIZE_CANNED_ERROR', "Sorry, this request could not be processed. Please try again later."),
        }
        desired_format = self._determine_format(request)
        serialized = self._serialize(request, data, desired_format)
        return response_class(content=serialized, content_type=build_content_type(desired_format))

    def _clean(self):
        """
        Hook for doing any extra handler-wide cleaning after Field.clean() been
        called on every field. Any ValidationError raised by this method will
        not be associated with a particular field; it will have a special-case
        association with the field named '__all__'.
        """
        return self._cleaned_data

    def _view(self, request, **kwargs):
        """
        A view for handling the various HTTP methods (GET/POST/PUT/DELETE).

        Relies on ``Resource.dispatch`` for the heavy-lifting.
        """
        return self._dispatch(request, **kwargs)

    def _dispatch(self, request, **kwargs):
        """
        Handles the common operations (allowed HTTP method, authentication,
        throttling, method lookup) surrounding most CRUD interactions.
        """
        allowed_methods = getattr(self._meta, "allowed_methods", None)
        request_method = self._method_check(request, allowed=allowed_methods)
        method = getattr(self, request_method, None)

        self._is_authenticated(request)
        self._is_authorized(request)
        self._throttle_check(request)

        # All clear. Process the request.
        request = convert_post_to_put(request)
        if self._is_valid():
            response = method(request, **kwargs)
        else:
            return http.HttpBadRequest()

        # Add the throttled request.
        self._log_throttled_access(request)

        # If what comes back isn't a ``HttpResponse``, assume that the
        # request was accepted and that some action occurred. This also
        # prevents Django from freaking out.
        if isinstance(response, HttpResponse):
            return response

        if response is None:
            return http.HttpNoContent()

        desired_format = self._determine_format(request)
        data = self._serialize(request, response, desired_format)
        response = HttpResponse(data, content_type=build_content_type(desired_format))

        return response

    def _method_check(self, request, allowed=None):
        """
        Ensures that the HTTP method used on the request is allowed to be
        handled by the resource.

        Takes an ``allowed`` parameter, which should be a list of lowercase
        HTTP methods to check against. Usually, this looks like::

            # The most generic lookup.
            self._method_check(request, self._meta.allowed_methods)

            # A lookup against what's allowed for list-type methods.
            self._method_check(request, self._meta.list_allowed_methods)

            # A useful check when creating a new endpoint that only handles
            # GET.
            self._method_check(request, ['get'])
        """
        if allowed is None:
            allowed = []

        request_method = request.method.lower()
        allows = ','.join(map(str.upper, allowed))

        if request_method == "options":
            response = HttpResponse(allows)
            response['Allow'] = allows
            raise ImmediateHttpResponse(response=response)

        if not request_method in allowed:
            response = http.HttpMethodNotAllowed(allows)
            response['Allow'] = allows
            raise ImmediateHttpResponse(response=response)

        return request_method

    def _is_authorized(self, request, object=None):
        """
        Handles checking of permissions to see if the user has authorization
        to GET, POST, PUT, or DELETE this resource.  If ``object`` is provided,
        the authorization backend can apply additional row-level permissions
        checking.
        """
        auth_result = self._meta.authorization.is_authorized(request, object)

        if isinstance(auth_result, HttpResponse):
            raise ImmediateHttpResponse(response=auth_result)

        if not auth_result is True:
            raise ImmediateHttpResponse(response=http.HttpUnauthorized())

    def _is_authenticated(self, request):
        """
        Handles checking if the user is authenticated and dealing with
        unauthenticated users.

        Mostly a hook, this uses class assigned to ``authentication`` from
        ``Resource._meta``.
        """
        # Authenticate the request as needed.
        auth_result = self._meta.authentication.is_authenticated(request)

        if isinstance(auth_result, HttpResponse):
            raise ImmediateHttpResponse(response=auth_result)

        if not auth_result is True:
            raise ImmediateHttpResponse(response=http.HttpUnauthorized())

    def _throttle_check(self, request):
        """
        Handles checking if the user should be throttled.

        Mostly a hook, this uses class assigned to ``throttle`` from
        ``Resource._meta``.
        """
        identifier = self._meta.authentication.get_identifier(request)

        # Check to see if they should be throttled.
        if self._meta.throttle.should_be_throttled(identifier):
            # Throttle limit exceeded.
            raise ImmediateHttpResponse(response=http.HttpTooManyRequests())

    def _log_throttled_access(self, request):
        """
        Handles the recording of the user's access for throttling purposes.

        Mostly a hook, this uses class assigned to ``throttle`` from
        ``Resource._meta``.
        """
        request_method = request.method.lower()
        self._meta.throttle.accessed(self._meta.authentication.get_identifier(request), url=request.get_full_path(), request_method=request_method)
    
    def _serialize(self, request, data, format, options=None):
        """
        Given a request, data and a desired format, produces a serialized
        version suitable for transfer over the wire.

        Mostly a hook, this uses the ``Serializer`` from ``Resource._meta``.
        """
        options = options or {}

        if 'text/javascript' in format:
            # get JSONP callback name. default to "callback"
            callback = request.GET.get('callback', 'callback')

            if not is_valid_jsonp_callback_value(callback):
                raise BadRequest('JSONP callback name is invalid.')

            options['callback'] = callback

        return self._meta.serializer.serialize(data, format, options)
    
    def _determine_format(self, request):
        """
        Used to determine the desired format.

        Largely relies on ``tastypie.utils.mime.determine_format`` but here
        as a point of extension.
        """
        return determine_format(request, self._meta.serializer, default_format=self._meta.default_format)

    def get(self, request, **kwargs):
        raise ImmediateHttpResponse(response=http.HttpMethodNotAllowed())
    
    def post(self, request, **kwargs):
        raise ImmediateHttpResponse(response=http.HttpMethodNotAllowed())
    
    def put(self, request, **kwargs):
        raise ImmediateHttpResponse(response=http.HttpMethodNotAllowed())
    
    def delete(self, request, **kwargs):
        raise ImmediateHttpResponse(response=http.HttpMethodNotAllowed())

    def patch(self, request, **kwargs):
        raise ImmediateHttpResponse(response=http.HttpMethodNotAllowed())


# Based off of ``piston.utils.coerce_put_post``. Similarly BSD-licensed.
# And no, the irony is not lost on me.
def convert_post_to_VERB(request, verb):
    """
    Force Django to process the VERB.
    """
    if request.method == verb:
        if hasattr(request, '_post'):
            del(request._post)
            del(request._files)

        try:
            request.method = "POST"
            request._load_post_and_files()
            request.method = verb
        except AttributeError:
            request.META['REQUEST_METHOD'] = 'POST'
            request._load_post_and_files()
            request.META['REQUEST_METHOD'] = verb
        setattr(request, verb, request.POST)

    return request


def convert_post_to_put(request):
    return convert_post_to_VERB(request, verb='PUT')


def convert_post_to_patch(request):
    return convert_post_to_VERB(request, verb='PATCH')


def determine_format(request, serializer, default_format='application/json'):
    """
    Tries to "smartly" determine which output format is desired.
    
    First attempts to find a ``format`` override from the request and supplies
    that if found.
    
    If no request format was demanded, it falls back to ``mimeparse`` and the
    ``Accepts`` header, allowing specification that way.
    
    If still no format is found, returns the ``default_format`` (which defaults
    to ``application/json`` if not provided).
    """
    # First, check if they forced the format.
    if request.GET.get('format'):
        if request.GET['format'] in serializer.formats:
            return serializer.get_mime_for_format(request.GET['format'])
    
    # If callback parameter is present, use JSONP.
    if request.GET.has_key('callback'):
        return serializer.get_mime_for_format('jsonp')
    
    # Try to fallback on the Accepts header.
    if request.META.get('HTTP_ACCEPT', '*/*') != '*/*':
        formats = list(serializer.supported_formats) or []
        # Reverse the list, because mimeparse is weird like that. See also
        # https://github.com/toastdriven/django-tastypie/issues#issue/12 for
        # more information.
        formats.reverse()
        best_format = mimeparse.best_match(formats, request.META['HTTP_ACCEPT'])
        
        if best_format:
            return best_format
    
    # No valid 'Accept' header/formats. Sane default.
    return default_format


def build_content_type(format, encoding='utf-8'):
    """
    Appends character encoding to the provided format if not already present.
    """
    if 'charset' in format:
        return format
    
    return "%s; charset=%s" % (format, encoding)
