"""Minimal Cloudflare API v4 client (Bearer token, JSON). The token is never included in errors."""
import json
from urllib.parse import urlencode

from .errors import ApiError

BASE = 'https://api.cloudflare.com/client/v4'


class CloudflareAPI(object):
    def __init__(self, token, transport, ui=None, dry_run=False):
        self._token = token
        self.http = transport
        self.ui = ui
        self.dry_run = dry_run

    def _scrub(self, text):
        return text.replace(self._token, '***') if self._token else text

    def _call(self, method, path, params=None, body=None):
        url = BASE + path + ('?' + urlencode(params) if params else '')
        headers = {'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json',
                   'Accept': 'application/json', 'User-Agent': 'rwsols-setup'}
        data = json.dumps(body).encode('utf8') if body is not None else None
        resp = self.http.request(method, url, headers=headers, body=data)
        try:
            doc = json.loads(resp.body.decode('utf8')) if resp.body else {}
        except ValueError:
            doc = {}
        if resp.status >= 400 or not doc.get('success', resp.status < 300):
            errs = doc.get('errors') or []
            detail = '; '.join('%s: %s' % (e.get('code'), e.get('message')) for e in errs if isinstance(e, dict))
            hint = ''
            if resp.status in (401, 403):
                hint = ' (the API token is invalid or lacks a required permission)'
            raise ApiError(self._scrub('Cloudflare API %s %s failed: HTTP %d%s%s' % (
                method, path, resp.status, hint, (': ' + detail) if detail else '')), resp.status, errs)
        return doc

    def get(self, path, params=None):
        return self._call('GET', path, params).get('result')

    def get_all(self, path, params=None, per_page=50):
        out, page = [], 1
        while True:
            p = dict(params or {})
            p.update({'page': page, 'per_page': per_page})
            doc = self._call('GET', path, p)
            res = doc.get('result') or []
            out.extend(res)
            info = doc.get('result_info') or {}
            total_pages = info.get('total_pages')
            if len(res) < per_page or (total_pages is not None and page >= total_pages) or page > 200:
                return out
            page += 1

    def mutate(self, method, path, body=None, dry_result=None):
        """POST/PUT/PATCH/DELETE. In dry-run mode prints the action and returns `dry_result`."""
        if self.dry_run:
            if self.ui:
                self.ui.say('[dry-run] would call Cloudflare API: %s %s' % (method, path))
            return dry_result
        return self._call(method, path, body=body).get('result')
