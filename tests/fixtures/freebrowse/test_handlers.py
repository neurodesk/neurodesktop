"""Exercise static assets with JupyterHub's real cookie/XSRF authentication."""

import logging
from pathlib import Path
import tempfile

from jupyter_server.auth import IdentityProvider, User
from jupyterhub.services.auth import HubOAuth
from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application, create_signed_value

from jupyterlab_freebrowse import handlers


SECRET = "freebrowse-test-cookie-secret"
TOKEN = "test-session-token"


class LocalHubOAuth(HubOAuth):
    async def user_for_token(self, token, **kwargs):
        # Only the Hub API lookup is replaced. Cookie verification and XSRF
        # decisions execute the installed JupyterHub implementation.
        return {"name": "alice"} if token == TOKEN else None


class HubIdentity(IdentityProvider):
    def __init__(self, base_url):
        super().__init__()
        self.hub = LocalHubOAuth(base_url=base_url, oauth_client_id="freebrowse-test", api_token="test-api-token")

    async def get_user(self, handler):
        self.hub._patch_xsrf(handler)
        user = await self.hub._get_user_cookie(handler)
        return User(username=user["name"]) if user else None


class HubAssetTests(AsyncHTTPTestCase):
    base_url = "/user/alice/"

    def get_app(self):
        self.files = tempfile.TemporaryDirectory()
        root = Path(self.files.name)
        (root / "index.html").write_text("<html>FreeBrowse viewer</html>")
        (root / "app.js").write_text("export const ready = true;")
        (root / "app.css").write_text("body { margin: 0; }")
        self.original_static_dir = handlers.STATIC_DIR
        handlers.STATIC_DIR = str(root)
        identity = HubIdentity(self.base_url)
        self.cookie = "freebrowse-test=" + create_signed_value(SECRET, "freebrowse-test", TOKEN).decode()
        app = Application(cookie_secret=SECRET, xsrf_cookies=True,
                          base_url=self.base_url, login_url=self.base_url + "login",
                          identity_provider=identity, log=logging.getLogger("freebrowse-test"),
                          allow_remote_access=True)
        handlers.setup_handlers(app)
        return app

    def tearDown(self):
        super().tearDown()
        handlers.STATIC_DIR = self.original_static_dir
        self.files.cleanup()

    def headers(self, site="same-origin", mode="cors", cookie=True):
        result = {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": mode}
        if cookie:
            result["Cookie"] = self.cookie
        return result

    def test_cookie_authenticated_modules_and_styles(self):
        for name in ("", "app.js", "app.css"):
            for method in ("GET", "HEAD"):
                with self.subTest(name=name, method=method):
                    response = self.fetch(self.base_url + "freebrowse/" + name,
                                          method=method, headers=self.headers(), follow_redirects=False)
                    assert response.code == 200, (response.code, response.body)
                    if method == "GET":
                        assert response.body

    def test_anonymous_and_invalid_cookie_are_rejected(self):
        for cookie in (None, "freebrowse-test=invalid"):
            for method in ("GET", "HEAD"):
                headers = self.headers(cookie=False)
                if cookie:
                    headers["Cookie"] = cookie
                response = self.fetch(self.base_url + "freebrowse/app.js", method=method,
                                      headers=headers, follow_redirects=False)
                assert response.code in (302, 403)

    def test_cross_site_and_missing_fetch_metadata_require_xsrf(self):
        for site in ("cross-site", "same-site", "none", ""):
            for method in ("GET", "HEAD"):
                response = self.fetch(self.base_url + "freebrowse/app.js", method=method,
                                      headers=self.headers(site=site), follow_redirects=False)
                assert response.code in (302, 403)

    def test_navigation_still_authenticates(self):
        response = self.fetch(self.base_url + "freebrowse/",
                              headers=self.headers(mode="navigate"), follow_redirects=False)
        assert response.code == 200

    def test_mutation_is_not_exempt_from_xsrf(self):
        response = self.fetch(self.base_url + "freebrowse/app.js", method="POST", body="",
                              headers=self.headers(), follow_redirects=False)
        assert response.code == 403

    def test_static_directory_cannot_escape(self):
        response = self.fetch(self.base_url + "freebrowse/%2e%2e/secret",
                              headers=self.headers(), follow_redirects=False)
        assert response.code in (403, 404)


class RootHubAssetTests(HubAssetTests):
    base_url = "/"


class DottedHubAssetTests(HubAssetTests):
    base_url = "/user/a.b/"
