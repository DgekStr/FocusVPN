import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from installer_utils import release_asset_sha256


class InstallerUtilsTests(unittest.TestCase):
    def test_release_digest_is_selected_by_exact_asset_name(self):
        expected = 'a684484d7477d1437282ee411f4d131d0340aaad60a7868841ebd5d87dd8a0c6'
        release = {'assets': [
            {'name': 'other.tar.gz', 'digest': 'sha256:' + '0' * 64},
            {'name': 'sing-box-1.14.2-linux-amd64.tar.gz', 'digest': 'sha256:' + expected},
        ]}
        self.assertEqual(release_asset_sha256(release, 'sing-box-1.14.2-linux-amd64.tar.gz'), expected)

    def test_missing_or_invalid_release_digest_fails_closed(self):
        for release, asset_name in (
            ({'assets': []}, 'sing-box.tar.gz'),
            ({'assets': [{'name': 'sing-box.tar.gz'}]}, 'sing-box.tar.gz'),
            ({'assets': [{'name': 'sing-box.tar.gz', 'digest': 'md5:' + '0' * 32}]}, 'sing-box.tar.gz'),
            ({'assets': [{'name': 'sing-box.tar.gz', 'digest': 'sha256:' + 'A' * 64}]}, 'sing-box.tar.gz'),
            ({}, 'sing-box.tar.gz'),
        ):
            with self.subTest(release=release), self.assertRaises(ValueError):
                release_asset_sha256(release, asset_name)


if __name__ == '__main__':
    unittest.main()