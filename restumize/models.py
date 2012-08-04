import datetime
import hmac
import time
from django.conf import settings
from django.db import models

try:
    from hashlib import sha1
except ImportError:
    import sha
    sha1 = sha.sha


class ApiAccess(models.Model):
    """A simple model for use with the ``CacheDBThrottle`` behaviors."""
    identifier = models.CharField(max_length=255)
    url = models.CharField(max_length=255, blank=True, default='')
    request_method = models.CharField(max_length=10, blank=True, default='')
    accessed = models.PositiveIntegerField()
    
    def __unicode__(self):
        return u"%s @ %s" % (self.identifer, self.accessed)
    
    def save(self, *args, **kwargs):
        self.accessed = int(time.time())
        return super(ApiAccess, self).save(*args, **kwargs)


if 'django.contrib.auth' in settings.INSTALLED_APPS:
    import uuid
    from django.conf import settings
    from django.contrib.auth.models import User
    
    class Token(models.Model):
        user = models.OneToOneField(User, related_name='api_token')
        token = models.CharField(max_length=256, blank=True, default='')
        created = models.DateTimeField(auto_now_add=True)

        def __unicode__(self):
            return u"%s for %s" % (self.token, self.user)
        
        def save(self, *args, **kwargs):
            if not self.token:
                self.token = self.generate_key()
            
            return super(Token, self).save(*args, **kwargs)
        
        def generate_key(self):
            while True:
                # Get a random UUID.
                new_uuid = uuid.uuid4()
                key = hmac.new(str(new_uuid), digestmod=sha1).hexdigest()
                if not token_exist(key):
                    break
            return key
    
    
    def create_api_token(sender, **kwargs):
        """
        A signal for hooking up automatic ``Token`` creation.
        """
        if kwargs.get('created') is True:
            Token.objects.create(user=kwargs.get('instance'))


    def token_exist(token_value):
            return Token.objects.filter(token=token_value).exists()

