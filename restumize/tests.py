
import datetime

from django.utils import unittest
from urlparse import urlparse
from django.conf import settings
from django.test import TestCase
from django.test.client import FakePayload, Client

from restumize.serializers import Serializer
from restumize import api, handler, fields


class TestHandler(handler.BaseHandler):
	class Meta:
		resource_name = 'test_handler'

	test_name = fields.CharField(max_length=300)
	test_datetime = fields.DateTimeField()
	test_date = fields.DateField()
	test_time = fields.TimeField()
	test_integer = fields.IntegerField()
	test_decimal = fields.DecimalField()
	test_float = fields.FloatField()
	test_email = fields.EmailField()
	test_url = fields.URLField()
	test_ip = fields.IPAddressField()
	test_slug = fields.SlugField()
	test_bool = fields.BooleanField()
	test_null_bool = fields.NullBooleanField()

	def get(self, request, **kwargs):
		data = {
			'status': 'ok',
			'code': 200,
		}
		return data


class DummyRequest():
	def __init__(self):
		self.now = datetime.datetime(2012, 8, 17, 14, 15, 45)

		self.GET = {
			'test_name': 'abc def',
			'test_datetime': self.now.strftime('%Y-%m-%d %H:%M:%S'),
			'test_date': self.now.strftime('%Y-%m-%d'),
			'test_time': self.now.strftime('%H:%M:%S'),
			'test_integer': '25',
			'test_decimal': '25.5',
			'test_float': '25.5',
			'test_email': 'test@example.com',
			'test_url': 'google.com',
			'test_ip': '127.0.0.1',
			'test_slug': 'this-is-a-slug',
			'test_bool': '1',
			'test_null_bool': '2',
			# 'format': 'json',
		}
		self.POST = self.GET
		self.FILES = {}
		self.META = {
			'REMOTE_ADDR': '127.0.0.1',
			'REMOTE_HOST': 'localhost',
			'SERVER_NAME': 'testserver',
			'HTTP_ACCEPT': 'application/json',
		}
		self.method = 'GET'
		self.path = '/test/case.html'

	def is_ajax(self):
		return True

	def has_header(self, header):
		return True

	def get_full_path(self):
		return self.path


class ResourceTestCase(unittest.TestCase):
	
	def setUp(self):

		self.request = DummyRequest()
		self.now_date = datetime.date(self.request.now.year, self.request.now.month, self.request.now.day)
		self.now_time = datetime.time(self.request.now.hour, self.request.now.minute, self.request.now.second)
		self.request.GET

		self.client = Client()

	def testHandler(self):
		import decimal
		from django.forms.util import from_current_timezone

		handler = TestHandler(self.request.GET);
		status = handler._is_valid()

		if not status:
			errors = handler.get_error_list()
			print errors, type(errors)
		
		self.assertEqual(status, True)
		self.assertEqual(handler._meta.resource_name, 'test_handler')
		self.assertEqual(handler.test_name, unicode(self.request.GET['test_name']))
		self.assertEqual(handler.test_datetime, from_current_timezone(self.request.now))
		self.assertEqual(handler.test_date, self.now_date)
		self.assertEqual(handler.test_time, self.now_time)
		self.assertEqual(handler.test_integer, int(self.request.GET['test_integer']))
		self.assertEqual(handler.test_decimal, decimal.Decimal(self.request.GET['test_decimal']))
		self.assertEqual(handler.test_float, float(self.request.GET['test_float']))
		self.assertEqual(handler.test_email, unicode(self.request.GET['test_email']))
		self.assertEqual(handler.test_url, unicode('http://' + self.request.GET['test_url'] + '/'))
		self.assertEqual(handler.test_ip, unicode(self.request.GET['test_ip']))
		self.assertEqual(handler.test_slug, unicode(self.request.GET['test_slug']))
		self.assertEqual(handler.test_bool, True)
		self.assertEqual(handler.test_null_bool, None)
		
	def testUrl(self):
		apiset = api.Api('test_api')
		apiset.register(TestHandler)
		resource_name = TestHandler._meta.resource_name

		urlconf = apiset.urls

		resource_class = apiset._registry[resource_name]
		view = apiset.wrap_view(resource_class)

