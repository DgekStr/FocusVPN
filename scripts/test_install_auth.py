import base64
import builtins
import getpass
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from create_panel_auth import create_auth_file


class InstallerAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'auth.json'

    def test_password_prompt_creates_valid_private_auth_file(self):
        responses = iter(('developer', 'correct horse battery staple', 'correct horse battery staple'))
        with patch.object(builtins, 'input', side_effect=lambda prompt: next(responses)), patch.object(getpass, 'getpass', side_effect=lambda prompt: next(responses)):
            create_auth_file(self.path)
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        salt = base64.b64decode(payload['salt'])
        expected = hashlib.scrypt(b'correct horse battery staple', salt=salt, n=2**14, r=8, p=1, dklen=32)
        self.assertEqual(payload['username'], 'developer')
        self.assertEqual(base64.b64decode(payload['hash']), expected)
        self.assertTrue(payload['realm'].startswith('FocusVPN '))
        self.assertTrue(payload['updated_at'])
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_invalid_passwords_or_username_never_create_file(self):
        for responses, error in (
            (('admin', 'long-enough-password', 'different-password'), 'do not match'),
            (('admin', 'short', 'short'), 'at least 12'),
            (('bad:name', 'correct horse battery staple', 'correct horse battery staple'), 'username'),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                values = iter(responses)
                create_auth_file(self.path, prompt=lambda prompt: next(values), read_secret=lambda prompt: next(values))
            self.assertFalse(self.path.exists())

    def test_existing_credentials_are_never_overwritten(self):
        self.path.write_text('{"existing": true}', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            create_auth_file(self.path)
        self.assertEqual(json.loads(self.path.read_text()), {'existing': True})


if __name__ == '__main__':
    unittest.main()