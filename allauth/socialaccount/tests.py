import random
try:
    from urllib.parse import urlparse, parse_qs
except ImportError:
    from urlparse import urlparse, parse_qs
import warnings
import json

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.urlresolvers import reverse
from django.test import TestCase, SimpleTestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings

from allauth.socialaccount.providers import registry

from ..tests import MockedResponse, mocked_response
from ..account import app_settings as account_settings
from ..account.models import EmailAddress
from ..account.utils import user_email, user_username
from ..utils import get_user_model, get_current_site

from .models import SocialLogin, SocialToken
from .helpers import complete_social_login
from .views import signup

import unittest
import allauth.socialaccount.app_settings as app_settings

from allauth.socialaccount.models import get_social_account_model, get_social_app_model
SocialAccount = get_social_account_model()
SocialApp = get_social_app_model()


def create_oauth_tests(provider):

    def get_mocked_response(self):
        pass

    def setUp(self):
        app = SocialApp.objects.create(provider=provider.id,
                                       name=provider.id,
                                       client_id='app123id',
                                       key=provider.id,
                                       secret='dummy')
        app.sites.add(get_current_site())

    @override_settings(SOCIALACCOUNT_AUTO_SIGNUP=False)
    def test_login(self):
        resp_mocks = self.get_mocked_response()
        if resp_mocks is None:
            warnings.warn("Cannot test provider %s, no oauth mock"
                          % self.provider.id)
            return
        resp = self.login(resp_mocks)
        self.assertRedirects(resp, reverse('socialaccount_signup'))
        resp = self.client.get(reverse('socialaccount_signup'))
        sociallogin = resp.context['form'].sociallogin
        data = dict(email=user_email(sociallogin.user),
                    username=str(random.randrange(1000, 10000000)))
        resp = self.client.post(reverse('socialaccount_signup'),
                                data=data)
        self.assertEqual('http://testserver/accounts/profile/',
                         resp['location'])
        user = resp.context['user']
        self.assertFalse(user.has_usable_password())
        return SocialAccount.objects.get(user=user,
                                         provider=self.provider.id)

    @override_settings(SOCIALACCOUNT_AUTO_SIGNUP=True,
                       SOCIALACCOUNT_EMAIL_REQUIRED=False,
                       ACCOUNT_EMAIL_REQUIRED=False)
    def test_auto_signup(self):
        resp_mocks = self.get_mocked_response()
        if not resp_mocks:
            warnings.warn("Cannot test provider %s, no oauth mock"
                          % self.provider.id)
            return
        resp = self.login(resp_mocks)
        self.assertEqual('http://testserver/accounts/profile/',
                         resp['location'])
        self.assertFalse(resp.context['user'].has_usable_password())

    def login(self, resp_mocks, process='login'):
        with mocked_response(MockedResponse(200,
                                            'oauth_token=token&'
                                            'oauth_token_secret=psst',
                                            {'content-type':
                                             'text/html'})):
            resp = self.client.get(reverse(self.provider.id + '_login'),
                                   dict(process=process))
        p = urlparse(resp['location'])
        q = parse_qs(p.query)
        complete_url = reverse(self.provider.id+'_callback')
        self.assertGreater(q['oauth_callback'][0]
                           .find(complete_url), 0)
        with mocked_response(self.get_access_token_response(),
                             *resp_mocks):
            resp = self.client.get(complete_url)
        return resp

    def get_access_token_response(self):
        return MockedResponse(
            200,
            'oauth_token=token&oauth_token_secret=psst',
            {'content-type': 'text/html'})

    def test_authentication_error(self):
        resp = self.client.get(reverse(self.provider.id + '_callback'))
        self.assertTemplateUsed(resp,
                                'socialaccount/authentication_error.html')

    impl = {'setUp': setUp,
            'login': login,
            'test_login': test_login,
            'get_mocked_response': get_mocked_response,
            'get_access_token_response': get_access_token_response,
            'test_authentication_error': test_authentication_error}
    class_name = 'OAuth2Tests_'+provider.id
    Class = type(class_name, (TestCase,), impl)
    Class.provider = provider
    return Class


def create_oauth2_tests(provider, cls=TestCase):

    def get_mocked_response(self):
        pass

    def get_login_response_json(self, with_refresh_token=True):
        rt = ''
        if with_refresh_token:
            rt = ',"refresh_token": "testrf"'
        return """{
            "uid":"weibo",
            "access_token":"testac"
            %s }""" % rt

    def setUp(self):
        app = SocialApp.objects.create(provider=provider.id,
                                       name=provider.id,
                                       client_id='app123id',
                                       key=provider.id,
                                       secret='dummy')
        app.sites.add(get_current_site())

    @override_settings(SOCIALACCOUNT_AUTO_SIGNUP=False)
    def test_login(self):
        resp_mock = self.get_mocked_response()
        if not resp_mock:
            warnings.warn("Cannot test provider %s, no oauth mock"
                          % self.provider.id)
            return
        resp = self.login(resp_mock,)
        self.assertRedirects(resp, reverse('socialaccount_signup'))

    def test_account_tokens(self, multiple_login=False):
        username = str(random.randrange(1000, 10000000))
        email = '%s@mail.com' % username
        user = get_user_model().objects.create(
            username=username,
            is_active=True,
            email=email)
        user.set_password('test')
        user.save()
        EmailAddress.objects.create(user=user,
                                    email=email,
                                    primary=True,
                                    verified=True)
        self.client.login(username=user.username,
                          password='test')
        self.login(self.get_mocked_response(), process='connect')
        if multiple_login:
            self.login(
                self.get_mocked_response(),
                with_refresh_token=False,
                process='connect')
        # get account
        sa = SocialAccount.objects.filter(user=user,
                                          provider=self.provider.id).get()
        # get token
        t = sa.socialtoken_set.get()
        # verify access_token and refresh_token
        self.assertEqual('testac', t.token)
        self.assertEqual(t.token_secret,
                         json.loads(self.get_login_response_json(
                             with_refresh_token=True)).get(
                                 'refresh_token', ''))

    def test_account_refresh_token_saved_next_login(self):
        '''
        fails if a login missing a refresh token, deletes the previously
        saved refresh token. Systems such as google's oauth only send
        a refresh token on first login.
        '''
        self.test_account_tokens(multiple_login=True)

    def login(self, resp_mock, process='login',
              with_refresh_token=True):
        resp = self.client.get(reverse(self.provider.id + '_login'),
                               dict(process=process))
        p = urlparse(resp['location'])
        q = parse_qs(p.query)
        complete_url = reverse(self.provider.id+'_callback')
        self.assertGreater(q['redirect_uri'][0]
                           .find(complete_url), 0)
        response_json = self \
            .get_login_response_json(with_refresh_token=with_refresh_token)
        with mocked_response(
                MockedResponse(
                    200,
                    response_json,
                    {'content-type': 'application/json'}),
                resp_mock):
            resp = self.client.get(complete_url,
                                   {'code': 'test',
                                    'state': q['state'][0]})
        return resp

    def test_authentication_error(self):
        resp = self.client.get(reverse(self.provider.id + '_callback'))
        self.assertTemplateUsed(resp,
                                'socialaccount/authentication_error.html')

    impl = {'setUp': setUp,
            'login': login,
            'test_login': test_login,
            'test_account_tokens': test_account_tokens,
            'test_account_refresh_token_saved_next_login':
            test_account_refresh_token_saved_next_login,
            'get_login_response_json': get_login_response_json,
            'get_mocked_response': get_mocked_response,
            'test_authentication_error': test_authentication_error}
    class_name = 'OAuth2Tests_'+provider.id
    Class = type(class_name, (cls,), impl)
    Class.provider = provider
    return Class


class SocialAccountTests(TestCase):
    def setUp(self):
        app1 = SocialApp.objects.create(provider='openid',
                                       name='openid',
                                       client_id='app123id',
                                       key='openid',
                                       secret='dummy')
        app1.sites.add(get_current_site())
        app1.save()
        app2 = SocialApp.objects.create(provider='twitter',
                                       name='twitter',
                                       client_id='app123id',
                                       key='twitter',
                                       secret='dummy')
        app2.sites.add(get_current_site())
        app2.save()

    @override_settings(
        SOCIALACCOUNT_AUTO_SIGNUP=True,
        ACCOUNT_SIGNUP_FORM_CLASS=None,
        ACCOUNT_EMAIL_VERIFICATION=account_settings.EmailVerificationMethod.NONE  # noqa
    )
    def test_email_address_created(self):
        factory = RequestFactory()
        request = factory.get('/accounts/login/callback/')
        request.user = AnonymousUser()
        SessionMiddleware().process_request(request)
        MessageMiddleware().process_request(request)

        User = get_user_model()
        user = User()
        setattr(user, account_settings.USER_MODEL_USERNAME_FIELD, 'test')
        setattr(user, account_settings.USER_MODEL_EMAIL_FIELD, 'test@test.com')

        account = SocialAccount(provider='openid', uid='123')
        sociallogin = SocialLogin(user=user, account=account)
        complete_social_login(request, sociallogin)

        user = User.objects.get(
            **{account_settings.USER_MODEL_USERNAME_FIELD: 'test'}
        )
        self.assertTrue(
            SocialAccount.objects.filter(user=user, uid=account.uid).exists()
        )
        self.assertTrue(
            EmailAddress.objects.filter(user=user,
                                        email=user_email(user)).exists()
        )

    @override_settings(
        ACCOUNT_EMAIL_REQUIRED=True,
        ACCOUNT_UNIQUE_EMAIL=True,
        ACCOUNT_USERNAME_REQUIRED=True,
        ACCOUNT_AUTHENTICATION_METHOD='email',
        SOCIALACCOUNT_AUTO_SIGNUP=True)
    def test_email_address_clash_username_required(self):
        """Test clash on both username and email"""
        request, resp = self._email_address_clash(
            'test',
            'test@test.com')
        self.assertEqual(
            resp['location'],
            reverse('socialaccount_signup'))

        # POST different username/email to social signup form
        request.method = 'POST'
        request.POST = {
            'username': 'other',
            'email': 'other@test.com'}
        resp = signup(request)
        self.assertEqual(
            resp['location'], '/accounts/profile/')
        user = get_user_model().objects.get(
            **{account_settings.USER_MODEL_EMAIL_FIELD:
               'other@test.com'})
        self.assertEqual(user_username(user), 'other')

    @override_settings(
        ACCOUNT_EMAIL_REQUIRED=True,
        ACCOUNT_UNIQUE_EMAIL=True,
        ACCOUNT_USERNAME_REQUIRED=False,
        ACCOUNT_AUTHENTICATION_METHOD='email',
        SOCIALACCOUNT_AUTO_SIGNUP=True)
    def test_email_address_clash_username_not_required(self):
        """Test clash while username is not required"""
        request, resp = self._email_address_clash(
            'test',
            'test@test.com')
        self.assertEqual(
            resp['location'],
            reverse('socialaccount_signup'))

        # POST email to social signup form (username not present)
        request.method = 'POST'
        request.POST = {
            'email': 'other@test.com'}
        resp = signup(request)
        self.assertEqual(
            resp['location'], '/accounts/profile/')
        user = get_user_model().objects.get(
            **{account_settings.USER_MODEL_EMAIL_FIELD:
               'other@test.com'})
        self.assertNotEqual(user_username(user), 'test')

    @override_settings(
        ACCOUNT_EMAIL_REQUIRED=True,
        ACCOUNT_UNIQUE_EMAIL=True,
        ACCOUNT_USERNAME_REQUIRED=False,
        ACCOUNT_AUTHENTICATION_METHOD='email',
        SOCIALACCOUNT_AUTO_SIGNUP=True)
    def test_email_address_clash_username_auto_signup(self):
        # Clash on username, but auto signup still works
        request, resp = self._email_address_clash('test', 'other@test.com')
        self.assertEqual(
            resp['location'], '/accounts/profile/')
        user = get_user_model().objects.get(
            **{account_settings.USER_MODEL_EMAIL_FIELD:
               'other@test.com'})
        self.assertNotEqual(user_username(user), 'test')

    def _email_address_clash(self, username, email):
        User = get_user_model()
        # Some existig user
        exi_user = User()
        user_username(exi_user, 'test')
        user_email(exi_user, 'test@test.com')
        exi_user.save()

        # A social user being signed up...
        account = SocialAccount(
            provider='twitter',
            uid='123')
        user = User()
        user_username(user, username)
        user_email(user, email)
        sociallogin = SocialLogin(user=user, account=account)

        # Signing up, should pop up the social signup form
        factory = RequestFactory()
        request = factory.get('/accounts/twitter/login/callback/')
        request.user = AnonymousUser()
        SessionMiddleware().process_request(request)
        MessageMiddleware().process_request(request)
        resp = complete_social_login(request, sociallogin)
        return request, resp


@override_settings(
    SOCIALACCOUNT_AUTO_SIGNUP=True,
    ACCOUNT_SIGNUP_FORM_CLASS=None,
    ACCOUNT_EMAIL_VERIFICATION=account_settings.EmailVerificationMethod.NONE,  # noqa
)
@unittest.skipIf(settings.SOCIALACCOUNT_SOCIAL_APP_MODEL == 'socialaccount.SocialApp',
                 'default SocialApp model, do not test swapping')
class SwapSocialAppTests(create_oauth2_tests(registry.by_id('google'), cls=TestCase)):
    def setUp(self):
        SocialApp = get_social_app_model()
        app = SocialApp.objects.create(provider=self.provider.id,
                                       name=self.provider.id,
                                       client_id='app123id',
                                       key=self.provider.id,
                                       new_field="testing",
                                       secret='dummy')
        app.sites.add(get_current_site())
        app.save()

    def get_mocked_response(self,
                            family_name='Penners',
                            given_name='Raymond',
                            name='Raymond Penners',
                            email='raymond.penners@gmail.com',
                            verified_email=True):
        return MockedResponse(200, """
              {"family_name": "%s", "name": "%s",
               "picture": "https://lh5.googleusercontent.com/-GOFYGBVOdBQ/AAAAAAAAAAI/AAAAAAAAAGM/WzRfPkv4xbo/photo.jpg",
               "locale": "nl", "gender": "male",
               "email": "%s",
               "link": "https://plus.google.com/108204268033311374519",
               "given_name": "%s", "id": "108204268033311374519",
               "verified_email": %s }
        """ % (family_name,
               name,
               email,
               given_name,
               (repr(verified_email).lower())))

    @override_settings(
        SOCIALACCOUNT_AUTO_SIGNUP=True,
        ACCOUNT_SIGNUP_FORM_CLASS=None,
        ACCOUNT_EMAIL_VERIFICATION=account_settings.EmailVerificationMethod.NONE,  # noqa
    )
    def test_get_social_app_model(self):
        from allauth.socialaccount.test_app.models import SocialAppSwapped
        self.assertEqual(get_social_app_model(), SocialAppSwapped)

    @override_settings(
        SOCIALACCOUNT_AUTO_SIGNUP=True,
        ACCOUNT_SIGNUP_FORM_CLASS=None,
        ACCOUNT_EMAIL_VERIFICATION=account_settings.EmailVerificationMethod.NONE,  # noqa
    )
    def test_swap_in_new_social_app(self):
        SocialApp = get_social_app_model()
        app = SocialApp.objects.filter(provider=self.provider.id).first()

        username = str(random.randrange(1000, 10000000))
        email = '%s@mail.com' % username
        user = get_user_model().objects.create(
            username=username,
            is_active=True,
            email=email)
        account = SocialAccount.objects.create(
            user=user,
            app=app,
            provider=self.provider.id,
            uid='123')
        token = SocialToken.objects.create(
            app=app,
            token='abc',
            account=account)
        self.assertEquals(app.new_field, 'testing')
        self.assertEquals(token.app, app)

        ## Just to explicitly test that the swapped app is called
        from allauth.socialaccount.test_app.models import SocialAppSwapped
        self.assertTrue(isinstance(app, SocialAppSwapped))
