#!/usr/bin/env python3
import base64
import json
from pathlib import Path
from urllib import error, request


class WgEasyApiError(RuntimeError):
    pass


class WgEasyApi:
    def __init__(self, secret_path, base_url='http://127.0.0.1:51821'):
        self.secret_path = Path(secret_path)
        self.base_url = base_url.rstrip('/')

    def _authorization(self):
        try:
            secret = json.loads(self.secret_path.read_text(encoding='utf-8'))
            username = secret['username']
            password = secret['password']
        except (KeyError, OSError, json.JSONDecodeError) as exc:
            raise WgEasyApiError('Служебный доступ к wg-easy не настроен.') from exc
        token = base64.b64encode(f'{username}:{password}'.encode('utf-8')).decode('ascii')
        return f'Basic {token}'

    def _request(self, method, path, payload=None, expect_json=True):
        if not path.startswith('/api/'):
            raise WgEasyApiError('Некорректный маршрут wg-easy.')
        data = None
        headers = {
            'Authorization': self._authorization(),
            'Accept': 'application/json',
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        http_request = request.Request(
            f'{self.base_url}{path}',
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(http_request, timeout=20) as response:
                body = response.read()
                response_headers = dict(response.headers.items())
        except error.HTTPError as exc:
            raise WgEasyApiError(f'wg-easy API вернул HTTP {exc.code}.') from exc
        except error.URLError as exc:
            raise WgEasyApiError('wg-easy API недоступен.') from exc
        if not expect_json:
            return body, response_headers
        if not body:
            return {}
        try:
            return json.loads(body.decode('utf-8'))
        except json.JSONDecodeError as exc:
            raise WgEasyApiError('wg-easy API вернул некорректный ответ.') from exc

    @staticmethod
    def client_id(client_id):
        try:
            client_id = int(client_id)
        except (TypeError, ValueError) as exc:
            raise WgEasyApiError('Некорректный идентификатор клиента.') from exc
        if client_id < 1:
            raise WgEasyApiError('Некорректный идентификатор клиента.')
        return client_id

    def clients(self):
        data = self._request('GET', '/api/client')
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get('clients'), list):
            return data['clients']
        raise WgEasyApiError('wg-easy API вернул неизвестный формат клиентов.')

    def client(self, client_id):
        return self._request('GET', f'/api/client/{self.client_id(client_id)}')

    def create_client(self, name, expires_at):
        payload = {'name': name, 'expiresAt': expires_at or None}
        return self._request('POST', '/api/client', payload)

    def update_client(self, client_id, payload):
        return self._request('POST', f'/api/client/{self.client_id(client_id)}', payload)

    def delete_client(self, client_id):
        return self._request('DELETE', f'/api/client/{self.client_id(client_id)}')

    def enable_client(self, client_id):
        return self._request('POST', f'/api/client/{self.client_id(client_id)}/enable')

    def disable_client(self, client_id):
        return self._request('POST', f'/api/client/{self.client_id(client_id)}/disable')

    def client_config(self, client_id):
        return self._request('GET', f'/api/client/{self.client_id(client_id)}/configuration', expect_json=False)

    def client_qr(self, client_id):
        return self._request('GET', f'/api/client/{self.client_id(client_id)}/qrcode.svg', expect_json=False)

    def general(self):
        return self._request('GET', '/api/admin/general')

    def update_general(self, payload):
        return self._request('POST', '/api/admin/general', payload)

    def interface(self):
        return self._request('GET', '/api/admin/interface')

    def update_interface(self, payload):
        return self._request('POST', '/api/admin/interface', payload)

    def restart_interface(self):
        return self._request('POST', '/api/admin/interface/restart')
