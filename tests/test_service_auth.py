import unittest

from southbound.service_auth import bearer_token, is_authorized


class ServiceAuthTest(unittest.TestCase):
    def test_bearer_token_parser(self) -> None:
        self.assertEqual(bearer_token("Bearer abc"), "abc")
        self.assertEqual(bearer_token("bearer  abc "), "abc")
        self.assertEqual(bearer_token("Basic abc"), "")
        self.assertEqual(bearer_token(None), "")

    def test_loopback_is_exempt_and_remote_requires_a_token(self) -> None:
        self.assertTrue(is_authorized("127.0.0.1", None, "secret"))
        self.assertTrue(is_authorized("::1", None, "secret"))
        self.assertFalse(is_authorized("192.168.1.50", None, "secret"))
        self.assertFalse(is_authorized("192.168.1.50", "Bearer wrong", "secret"))
        self.assertTrue(is_authorized("192.168.1.50", "Bearer secret", "secret"))

    def test_empty_expected_token_keeps_loopback_mode_open(self) -> None:
        self.assertTrue(is_authorized("192.168.1.50", None, ""))
        self.assertTrue(is_authorized("127.0.0.1", None, ""))


if __name__ == "__main__":
    unittest.main()
