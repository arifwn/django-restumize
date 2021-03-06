import operator


class Authorization(object):
    """
    A base class that provides no permissions checking.
    """
    def __get__(self, instance, owner):
        """
        Makes ``Authorization`` a descriptor of ``ResourceOptions`` and creates
        a reference to the ``ResourceOptions`` object that may be used by
        methods of ``Authorization``.
        """
        self.resource_meta = instance
        return self

    def is_authorized(self, request, object=None):
        """
        Checks if the user is authorized to perform the request. If ``object``
        is provided, it can do additional row-level checks.

        Should return either ``True`` if allowed, ``False`` if not or an
        ``HttpResponse`` if you need something custom.
        """
        return True


class ReadOnlyAuthorization(Authorization):
    """
    Default Authentication class for ``Resource`` objects.

    Only allows GET requests.
    """

    def is_authorized(self, request, object=None):
        """
        Allow any ``GET`` request.
        """
        if request.method == 'GET':
            return True
        else:
            return False


class ReadWriteAuthorization(Authorization):
    """
    Allows GET and POST requests.
    """
    allowed_method = ['GET', 'POST']

    def is_authorized(self, request, object=None):
        """
        Allow any ``GET`` request.
        """
        if request.method in self.allowed_method:
            return True
        else:
            return False


class AdminAuthorization(Authorization):
    """
    Only allows request from admin accounts.
    """

    def is_authorized(self, request, object=None):
        """
        Allow any request made by admin.
        """
        if request.user.is_staff:
            return True
        else:
            return False
