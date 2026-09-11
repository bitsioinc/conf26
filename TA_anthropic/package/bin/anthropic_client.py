"""Pure REST client for the Anthropic Admin API. No Splunk imports.

Kept deliberately dependency-light (only ``requests``, and even that is
imported lazily) so it can be unit tested without a Splunk runtime.

Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25.

Two different pagination schemes live in the Admin API and they must not be
conflated:

* Report endpoints (usage_report/messages, cost_report) return
  ``has_more`` + ``next_page``; the token is echoed back as the ``page``
  query parameter.
* Directory endpoints (api_keys, users) return ``has_more`` + ``last_id``;
  the id is echoed back as the ``after_id`` query parameter.
"""
import time

ANTHROPIC_VERSION = "2023-06-01"
USER_AGENT = "TA_anthropic/0.1.0 (Splunk add-on)"

USAGE_REPORT_PATH = "/v1/organizations/usage_report/messages"
COST_REPORT_PATH = "/v1/organizations/cost_report"
API_KEYS_PATH = "/v1/organizations/api_keys"
USERS_PATH = "/v1/organizations/users"

DIRECTORY_PAGE_LIMIT = 100


class AnthropicAPIError(Exception):
    """Raised when the Admin API returns a non-200 after the single retry."""

    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super(AnthropicAPIError, self).__init__(
            "Admin API error {}: {}".format(status_code, message)
        )


class AnthropicAdminClient(object):
    """Minimal Admin API client: paginated reports plus directory listings."""

    def __init__(self, api_key, base_url="https://api.anthropic.com", session=None,
                 timeout=30, retry_backoff=2):
        if session is None:
            import requests
            session = requests.Session()
        self._session = session
        self._timeout = timeout
        self._retry_backoff = retry_backoff
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "User-Agent": USER_AGENT,
        }

    # -- transport ---------------------------------------------------------

    def _get(self, path, params):
        url = "{}{}".format(self._base_url, path)
        resp = self._session.get(url, headers=self._headers, params=params,
                                 timeout=self._timeout)
        if resp.status_code == 429 or resp.status_code >= 500:
            # Exactly one retry, then give up.
            if self._retry_backoff:
                time.sleep(self._retry_backoff)
            resp = self._session.get(url, headers=self._headers, params=params,
                                     timeout=self._timeout)
        if resp.status_code != 200:
            raise AnthropicAPIError(resp.status_code, resp.text[:500])
        return resp.json()

    # -- report pagination (has_more -> next_page -> ?page=) ---------------

    def _iter_report(self, path, params):
        page_token = None
        while True:
            page_params = dict(params)
            if page_token:
                page_params["page"] = page_token
            page = self._get(path, page_params)
            yield page
            if not page.get("has_more"):
                return
            page_token = page.get("next_page")
            if not page_token:
                return

    def iter_usage_report(self, starting_at, ending_at, bucket_width="1h", group_by=None):
        params = {
            "starting_at": starting_at,
            "ending_at": ending_at,
            "bucket_width": bucket_width,
        }
        if group_by:
            params["group_by[]"] = list(group_by)
        return self._iter_report(USAGE_REPORT_PATH, params)

    def iter_cost_report(self, starting_at, ending_at, group_by=None):
        params = {
            "starting_at": starting_at,
            "ending_at": ending_at,
            "bucket_width": "1d",
        }
        if group_by:
            params["group_by[]"] = list(group_by)
        return self._iter_report(COST_REPORT_PATH, params)

    # -- directory pagination (has_more -> last_id -> ?after_id=) ----------

    def _list_directory(self, path):
        items = []
        after_id = None
        while True:
            params = {"limit": DIRECTORY_PAGE_LIMIT}
            if after_id:
                params["after_id"] = after_id
            page = self._get(path, params)
            items.extend(page.get("data", []))
            if not page.get("has_more"):
                return items
            after_id = page.get("last_id")
            if not after_id:
                return items

    def list_api_keys(self):
        return self._list_directory(API_KEYS_PATH)

    def list_users(self):
        return self._list_directory(USERS_PATH)
