"""No-network and no-credential local staging boot smoke."""
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from ui.server import JarvisServer


def test_local_service_starts_with_no_credentials_and_denies_operational_api(tmp_path):
    with patch.object(Path, 'home', return_value=tmp_path):
        server = JarvisServer(port=0)
        server.start()
        try:
            base = 'http://127.0.0.1:%s' % server.httpd.server_address[1]
            with urllib.request.urlopen(base + '/', timeout=3) as response:
                assert response.status == 200
                assert b'<html' in response.read().lower()
            with urllib.request.urlopen(base + '/api/auth/status', timeout=3) as response:
                assert response.status == 200
            try:
                urllib.request.urlopen(base + '/api/capabilities', timeout=3)
            except urllib.error.HTTPError as error:
                assert error.code in (401, 503)
            else:
                raise AssertionError('Operational API must not expose capabilities without login')
            assert server.httpd.server_address[0] == '127.0.0.1'
        finally:
            server.stop()
