import unittest
from unittest import mock

import fakes  # noqa: F401  (puts the repo on sys.path)

from rwsols_setup import runtime


class Response(object):
    status, headers = 200, {}

    def read(self):
        return b'ok'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class UserAgentTests(unittest.TestCase):
    def sent(self, headers):
        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch('urllib.request.build_opener', return_value=opener):
            runtime.UrllibTransport().request('GET', 'https://pkg.example.com/key.gpg', headers=headers)
        return dict(opener.open.call_args[0][0].header_items())

    def test_requests_without_headers_still_name_the_client(self):
        # urllib's default User-Agent gets HTTP 403 from Cloudflare's package server.
        self.assertEqual(self.sent(None)['User-agent'], 'rwsols-setup')

    def test_a_caller_supplied_user_agent_is_kept(self):
        self.assertEqual(self.sent({'user-agent': 'custom'})['User-agent'], 'custom')


if __name__ == '__main__':
    unittest.main()
