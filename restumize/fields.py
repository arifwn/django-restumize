
import copy
import datetime
import urlparse
from decimal import Decimal, DecimalException

from django.core import exceptions, validators
from django.core.exceptions import ValidationError
from django.forms.util import from_current_timezone, to_current_timezone
from django.utils.encoding import smart_unicode, smart_str, force_unicode
from django.utils import formats
from django.utils.translation import ugettext_lazy as _


class BaseField(object):
    default_validators = [] # Default set of validators
    default_error_messages = {
        'required': _(u'This field is required.'),
        'invalid': _(u'Enter a valid value.'),
    }

    def __init__(self, default=None, required=True, error_messages=None, validators=[], localize=False):
        self.default = default
        self.required = required
        self.validators = self.default_validators + validators
        self.localize = localize

        messages = {}
        for c in reversed(self.__class__.__mro__):
            messages.update(getattr(c, 'default_error_messages', {}))
        messages.update(error_messages or {})
        self.error_messages = messages

    def to_python(self, value):
        """
        Converts the input value into the expected Python data type, raising
        django.core.exceptions.ValidationError if the data can't be converted.
        Returns the converted value. Subclasses should override this.
        """
        return value

    def run_validators(self, value):
        if value in validators.EMPTY_VALUES:
            return

        errors = []
        for v in self.validators:
            try:
                v(value)
            except exceptions.ValidationError, e:
                if hasattr(e, 'code') and e.code in self.error_messages:
                    message = self.error_messages[e.code]
                    if e.params:
                        message = message % e.params
                    errors.append(message)
                else:
                    errors.extend(e.messages)
        if errors:
            raise exceptions.ValidationError(errors)

    def clean(self, value):
        value = self.to_python(value)
        self.validate(value)
        self.run_validators(value)
        return value

    def validate(self, value):
        if value in validators.EMPTY_VALUES and self.required:
            raise ValidationError(self.error_messages['required'])

    def __deepcopy__(self, memo):
        result = copy.copy(self)
        memo[id(self)] = result
        result.validators = self.validators[:]
        return result


class CharField(BaseField):

    def __init__(self, max_length=None, min_length=None, *args, **kwargs):
        super(CharField, self).__init__(*args, **kwargs)
        if min_length is not None:
            self.validators.append(validators.MinLengthValidator(min_length))
        if max_length is not None:
            self.validators.append(validators.MaxLengthValidator(max_length))
        if self.default is None:
            # do not return None, return empty unicode string instead
            self.default = u''

    def to_python(self, value):
        "Returns a Unicode object."
        if value in validators.EMPTY_VALUES:
            return self.default
        return smart_unicode(value)


class IntegerField(BaseField):
    default_error_messages = {
        'invalid': _(u'Enter a whole number.'),
        'max_value': _(u'Ensure this value is less than or equal to %(limit_value)s.'),
        'min_value': _(u'Ensure this value is greater than or equal to %(limit_value)s.'),
    }

    def __init__(self, max_value=None, min_value=None, *args, **kwargs):
        self.max_value, self.min_value = max_value, min_value
        super(IntegerField, self).__init__(*args, **kwargs)

        if max_value is not None:
            self.validators.append(validators.MaxValueValidator(max_value))
        if min_value is not None:
            self.validators.append(validators.MinValueValidator(min_value))

    def to_python(self, value):
        """
        Validates that int() can be called on the input. Returns the result
        of int(). Returns None for empty values.
        """
        value = super(IntegerField, self).to_python(value)
        if value in validators.EMPTY_VALUES:
            return self.default
        if self.localize:
            value = formats.sanitize_separators(value)
        try:
            value = int(str(value))
        except (ValueError, TypeError):
            raise ValidationError(self.error_messages['invalid'])
        return value


class FloatField(IntegerField):
    default_error_messages = {
        'invalid': _(u'Enter a number.'),
    }

    def to_python(self, value):
        """
        Validates that float() can be called on the input. Returns the result
        of float(). Returns None for empty values.
        """
        value = super(IntegerField, self).to_python(value)
        if value in validators.EMPTY_VALUES:
            return self.default
        if self.localize:
            value = formats.sanitize_separators(value)
        try:
            value = float(value)
        except (ValueError, TypeError):
            raise ValidationError(self.error_messages['invalid'])
        return value


class DecimalField(BaseField):
    default_error_messages = {
        'invalid': _(u'Enter a number.'),
        'max_value': _(u'Ensure this value is less than or equal to %(limit_value)s.'),
        'min_value': _(u'Ensure this value is greater than or equal to %(limit_value)s.'),
        'max_digits': _('Ensure that there are no more than %s digits in total.'),
        'max_decimal_places': _('Ensure that there are no more than %s decimal places.'),
        'max_whole_digits': _('Ensure that there are no more than %s digits before the decimal point.')
    }

    def __init__(self, max_value=None, min_value=None, max_digits=None, decimal_places=None, *args, **kwargs):
        self.max_value, self.min_value = max_value, min_value
        self.max_digits, self.decimal_places = max_digits, decimal_places
        super(DecimalField, self).__init__(self, *args, **kwargs)

        if max_value is not None:
            self.validators.append(validators.MaxValueValidator(max_value))
        if min_value is not None:
            self.validators.append(validators.MinValueValidator(min_value))

    def to_python(self, value):
        """
        Validates that the input is a decimal number. Returns a Decimal
        instance. Returns None for empty values. Ensures that there are no more
        than max_digits in the number, and no more than decimal_places digits
        after the decimal point.
        """
        if value in validators.EMPTY_VALUES:
            return self.default
        if self.localize:
            value = formats.sanitize_separators(value)
        value = smart_str(value).strip()
        try:
            value = Decimal(value)
        except DecimalException:
            raise ValidationError(self.error_messages['invalid'])
        return value

    def validate(self, value):
        super(DecimalField, self).validate(value)
        if value in validators.EMPTY_VALUES:
            return
        # Check for NaN, Inf and -Inf values. We can't compare directly for NaN,
        # since it is never equal to itself. However, NaN is the only value that
        # isn't equal to itself, so we can use this to identify NaN
        if value != value or value == Decimal("Inf") or value == Decimal("-Inf"):
            raise ValidationError(self.error_messages['invalid'])
        sign, digittuple, exponent = value.as_tuple()
        decimals = abs(exponent)
        # digittuple doesn't include any leading zeros.
        digits = len(digittuple)
        if decimals > digits:
            # We have leading zeros up to or past the decimal point.  Count
            # everything past the decimal point as a digit.  We do not count
            # 0 before the decimal point as a digit since that would mean
            # we would not allow max_digits = decimal_places.
            digits = decimals
        whole_digits = digits - decimals

        if self.max_digits is not None and digits > self.max_digits:
            raise ValidationError(self.error_messages['max_digits'] % self.max_digits)
        if self.decimal_places is not None and decimals > self.decimal_places:
            raise ValidationError(self.error_messages['max_decimal_places'] % self.decimal_places)
        if self.max_digits is not None and self.decimal_places is not None and whole_digits > (self.max_digits - self.decimal_places):
            raise ValidationError(self.error_messages['max_whole_digits'] % (self.max_digits - self.decimal_places))
        return value


class BaseTemporalField(BaseField):
    def __init__(self, input_formats=None, *args, **kwargs):
        super(BaseTemporalField, self).__init__(*args, **kwargs)
        if input_formats is not None:
            self.input_formats = input_formats

    def to_python(self, value):
        # Try to coerce the value to unicode.
        unicode_value = force_unicode(value, strings_only=True)
        if isinstance(unicode_value, unicode):
            value = unicode_value.strip()
        # If unicode, try to strptime against each input format.
        if isinstance(value, unicode):
            for format in self.input_formats:
                try:
                    return self.strptime(value, format)
                except ValueError:
                    if format.endswith('.%f'):
                        # Compatibility with datetime in pythons < 2.6.
                        # See: http://docs.python.org/library/datetime.html#strftime-and-strptime-behavior
                        if value.count('.') != format.count('.'):
                            continue
                        try:
                            datetime_str, usecs_str = value.rsplit('.', 1)
                            usecs = int(usecs_str[:6].ljust(6, '0'))
                            dt = datetime.datetime.strptime(datetime_str, format[:-3])
                            return dt.replace(microsecond=usecs)
                        except ValueError:
                            continue
        raise ValidationError(self.error_messages['invalid'])

    def strptime(self, value, format):
        raise NotImplementedError('Subclasses must define this method.')


class DateField(BaseTemporalField):
    input_formats = formats.get_format_lazy('DATE_INPUT_FORMATS')
    default_error_messages = {
        'invalid': _(u'Enter a valid date.'),
    }

    def to_python(self, value):
        """
        Validates that the input can be converted to a date. Returns a Python
        datetime.date object.
        """
        if value in validators.EMPTY_VALUES:
            return self.default
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        return super(DateField, self).to_python(value)

    def strptime(self, value, format):
        return datetime.datetime.strptime(value, format).date()


class TimeField(BaseTemporalField):
    input_formats = formats.get_format_lazy('TIME_INPUT_FORMATS')
    default_error_messages = {
        'invalid': _(u'Enter a valid time.')
    }

    def to_python(self, value):
        """
        Validates that the input can be converted to a time. Returns a Python
        datetime.time object.
        """
        if value in validators.EMPTY_VALUES:
            return self.default
        if isinstance(value, datetime.time):
            return value
        return super(TimeField, self).to_python(value)

    def strptime(self, value, format):
        return datetime.datetime.strptime(value, format).time()


class DateTimeField(BaseTemporalField):
    input_formats = formats.get_format_lazy('DATETIME_INPUT_FORMATS')
    default_error_messages = {
        'invalid': _(u'Enter a valid date/time.'),
    }

    def prepare_value(self, value):
        if isinstance(value, datetime.datetime):
            value = to_current_timezone(value)
        return value

    def to_python(self, value):
        """
        Validates that the input can be converted to a datetime. Returns a
        Python datetime.datetime object.
        """
        if value in validators.EMPTY_VALUES:
            return self.default
        if isinstance(value, datetime.datetime):
            return from_current_timezone(value)
        if isinstance(value, datetime.date):
            result = datetime.datetime(value.year, value.month, value.day)
            return from_current_timezone(result)
        if isinstance(value, list):
            # Input comes from a SplitDateTimeWidget, for example. So, it's two
            # components: date and time.
            if value in validators.EMPTY_VALUES and value[1] in validators.EMPTY_VALUES:
                return self.default
        result = super(DateTimeField, self).to_python(value)
        return from_current_timezone(result)

    def strptime(self, value, format):
        return datetime.datetime.strptime(value, format)

class EmailField(CharField):
    default_validators = [validators.validate_email]
    default_error_messages = {
        'invalid': _(u'Enter a valid e-mail address.'),
    }

    def clean(self, value):
        value = self.to_python(value).strip()
        return super(EmailField, self).clean(value)


class URLField(CharField):
    default_error_messages = {
        'invalid': _(u'Enter a valid URL.'),
        'invalid_link': _(u'This URL appears to be a broken link.'),
    }

    def __init__(self, verify_exists=False, validator_user_agent=validators.URL_VALIDATOR_USER_AGENT, *args, **kwargs):
        super(URLField, self).__init__(*args, **kwargs)
        self.validators.append(validators.URLValidator(verify_exists=verify_exists, validator_user_agent=validator_user_agent))

    def to_python(self, value):

        def split_url(url):
            """
            Returns a list of url parts via ``urlparse.urlsplit`` (or raises a
            ``ValidationError`` exception for certain).
            """
            try:
                return list(urlparse.urlsplit(url))
            except ValueError:
                # urlparse.urlsplit can raise a ValueError with some
                # misformatted URLs.
                raise ValidationError(self.error_messages['invalid'])

        value = super(URLField, self).to_python(value)
        if value:
            url_fields = split_url(value)
            if not url_fields[0]:
                # If no URL scheme given, assume http://
                url_fields[0] = 'http'
            if not url_fields[1]:
                # Assume that if no domain is provided, that the path segment
                # contains the domain.
                url_fields[1] = url_fields[2]
                url_fields[2] = ''
                # Rebuild the url_fields list, since the domain segment may now
                # contain the path too.
                url_fields = split_url(urlparse.urlunsplit(url_fields))
            if not url_fields[2]:
                # the path portion may need to be added before query params
                url_fields[2] = '/'
            value = urlparse.urlunsplit(url_fields)
        return value


class IPAddressField(CharField):
    default_error_messages = {
        'invalid': _(u'Enter a valid IPv4 address.'),
    }
    default_validators = [validators.validate_ipv4_address]


class SlugField(CharField):
    default_error_messages = {
        'invalid': _(u"Enter a valid 'slug' consisting of letters, numbers,"
                     u" underscores or hyphens."),
    }
    default_validators = [validators.validate_slug]


class BooleanField(BaseField):

    def to_python(self, value):
        """Returns a Python boolean object."""
        # Explicitly check for the string 'False', which is what a hidden field
        # will submit for False. Also check for '0', since this is what
        # RadioSelect will provide. Because bool("True") == bool('1') == True,
        # we don't need to handle that explicitly.
        if isinstance(value, basestring) and value.lower() in ('false', '0'):
            value = False
        else:
            value = bool(value)
        value = super(BooleanField, self).to_python(value)
        if not value and self.required:
            raise ValidationError(self.error_messages['required'])
        return value


class NullBooleanField(BooleanField):
    """
    A field whose valid values are None, True and False. Invalid values are
    cleaned to None.
    """

    def to_python(self, value):
        """
        Explicitly checks for the string 'True' and 'False', which is what a
        hidden field will submit for True and False, and for '1' and '0', which
        is what a RadioField will submit. Unlike the Booleanfield we need to
        explicitly check for True, because we are not using the bool() function
        """
        if value in (True, 'True', '1'):
            return True
        elif value in (False, 'False', '0'):
            return False
        else:
            return self.default

    def validate(self, value):
        pass


class FileField(BaseField):
    default_error_messages = {
        'invalid': _(u"No file was submitted. Check the encoding type on the form."),
        'missing': _(u"No file was submitted."),
        'empty': _(u"The submitted file is empty."),
        'max_length': _(u'Ensure this filename has at most %(max)d characters (it has %(length)d).'),
        'contradiction': _(u'Please either submit a file or check the clear checkbox, not both.')
    }

    def __init__(self, *args, **kwargs):
        self.max_length = kwargs.pop('max_length', None)
        self.allow_empty_file = kwargs.pop('allow_empty_file', False)
        super(FileField, self).__init__(*args, **kwargs)

    def to_python(self, data):
        if data in validators.EMPTY_VALUES:
            return self.default

        # UploadedFile objects should have name and size attributes.
        try:
            file_name = data.name
            file_size = data.size
        except AttributeError:
            raise ValidationError(self.error_messages['invalid'])

        if self.max_length is not None and len(file_name) > self.max_length:
            error_values =  {'max': self.max_length, 'length': len(file_name)}
            raise ValidationError(self.error_messages['max_length'] % error_values)
        if not file_name:
            raise ValidationError(self.error_messages['invalid'])
        if not self.allow_empty_file and not file_size:
            raise ValidationError(self.error_messages['empty'])

        return data


class ImageField(FileField):
    default_error_messages = {
        'invalid_image': _(u"Upload a valid image. The file you uploaded was either not an image or a corrupted image."),
    }

    def to_python(self, data):
        """
        Checks that the file-upload field data contains a valid image (GIF, JPG,
        PNG, possibly others -- whatever the Python Imaging Library supports).
        """
        f = super(ImageField, self).to_python(data)
        if f is None:
            return self.default

        # Try to import PIL in either of the two ways it can end up installed.
        try:
            from PIL import Image
        except ImportError:
            import Image

        # We need to get a file object for PIL. We might have a path or we might
        # have to read the data into memory.
        if hasattr(data, 'temporary_file_path'):
            file = data.temporary_file_path()
        else:
            if hasattr(data, 'read'):
                file = StringIO(data.read())
            else:
                file = StringIO(data['content'])

        try:
            # load() is the only method that can spot a truncated JPEG,
            #  but it cannot be called sanely after verify()
            trial_image = Image.open(file)
            trial_image.load()

            # Since we're about to use the file again we have to reset the
            # file object if possible.
            if hasattr(file, 'reset'):
                file.reset()

            # verify() is the only method that can spot a corrupt PNG,
            #  but it must be called immediately after the constructor
            trial_image = Image.open(file)
            trial_image.verify()
        except ImportError:
            # Under PyPy, it is possible to import PIL. However, the underlying
            # _imaging C module isn't available, so an ImportError will be
            # raised. Catch and re-raise.
            raise
        except Exception: # Python Imaging Library doesn't recognize it as an image
            raise ValidationError(self.error_messages['invalid_image'])
        if hasattr(f, 'seek') and callable(f.seek):
            f.seek(0)
        return f


def is_empty(value):
    if value in validators.EMPTY_VALUES:
        return True
    else:
        return False