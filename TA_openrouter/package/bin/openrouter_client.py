"""Pure REST client for the OpenRouter management API. No Splunk imports.

Dependency-light (only ``requests``, imported lazily) so it unit tests without
a Splunk runtime. Python 3.9 compatible: Splunk's app runtime is CPython
3.9.25.

Two things differ from the Anthropic client and are easy to get wrong:

* Auth is ``Authorization: Bearer``, not ``x-api-key``. Management keys carry
  the same ``sk-or-v1-`` prefix as ordinary inference keys, so a 401 may mean
  "right string, wrong key type" rather than a typo.
* Analytics is a POST with a query body, and the response row shape depends on
  what was asked for. The client returns the body verbatim; interpreting it is
  openrouter_transform's job.
"""
import time

USER_AGENT = "TA_openrouter/0.1.0 (Splunk add-on)"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

ANALYTICS_QUERY_PATH = "/analytics/query"
KEYS_PATH = "/keys"

#: Pagination terminates when the server returns an empty page. This cap is a
#: runaway guard in case of server bugs or unexpected behavior, not a
#: documented limit. Raising on overflow is loud and consistent with how the
#: module treats unrecoverable failures.
KEYS_MAX_PAGES = 10000

#: The server defaults to 1000 rows but rejects anything above 10000 with a
#: 400 (verified against a live collection run:
#: {"error": {"message": "limit: Too big: expected number to be <=10000",
#: "code": 400}}). An hour x key x model grid can still cross 10000 with
#: enough keys and models, and an overflow there is reported as
#: metadata.truncated rather than an error -- the checkpoint holds and the
#: next interval picks up where this one left off -- so ask for the API's
#: actual maximum rather than the server's smaller default.
DEFAULT_ROW_LIMIT = 10000


class OpenRouterAPIError(Exception):
    """Raised when the management API returns a non-200 we cannot recover."""

    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super(OpenRouterAPIError, self).__init__(
            "OpenRouter API error {}: {}".format(status_code, message)
        )


class OpenRouterClient(object):
    """Analytics queries plus the API key roster."""

    def __init__(self, api_key, base_url=DEFAULT_BASE_URL, session=None,
                 timeout=30, retry_backoff=2):
        if session is None:
            import requests
            session = requests.Session()
        self._session = session
        self._timeout = timeout
        self._retry_backoff = retry_backoff
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": "Bearer {}".format(api_key),
            "User-Agent": USER_AGENT,
        }

    # -- transport ---------------------------------------------------------

    def _retryable(self, status_code):
        return status_code == 429 or status_code >= 500

    def _request(self, method, path, params=None, body=None):
        url = "{}{}".format(self._base_url, path)

        def send():
            if method == "POST":
                return self._session.post(url, headers=self._headers,
                                          json=body, timeout=self._timeout)
            return self._session.get(url, headers=self._headers,
                                     params=params, timeout=self._timeout)

        resp = send()
        if self._retryable(resp.status_code):
            # Exactly one retry, then give up. 4xx other than 429 is permanent
            # (a wrong key type will never succeed) so it is not retried.
            if self._retry_backoff:
                time.sleep(self._retry_backoff)
            resp = send()
        if resp.status_code != 200:
            raise OpenRouterAPIError(resp.status_code, resp.text[:500])
        return resp.json()

    # -- analytics ---------------------------------------------------------

    def query_analytics(self, metrics, dimensions, granularity, start, end,
                        limit=None):
        """POST /analytics/query. Returns the response body verbatim."""
        body = {
            "metrics": list(metrics),
            "dimensions": list(dimensions),
            "granularity": granularity,
            "time_range": {"start": start, "end": end},
            "limit": limit or DEFAULT_ROW_LIMIT,
        }
        return self._request("POST", ANALYTICS_QUERY_PATH, body=body)

    # -- keys (offset pagination) ------------------------------------------

    def list_keys(self, include_disabled=True):
        """GET /keys, following offset pagination to exhaustion.

        Only the default workspace is returned; multi-workspace enumeration
        via workspace_id is out of scope for v0.1.0.
        """
        items = []
        offset = 0
        page_count = 0
        while True:
            page_count += 1
            if page_count > KEYS_MAX_PAGES:
                raise OpenRouterAPIError(
                    500,
                    "Pagination exceeded {}: possible server bug".format(KEYS_MAX_PAGES)
                )
            params = {
                "offset": offset,
                "include_disabled": "true" if include_disabled else "false",
            }
            page = self._request("GET", KEYS_PATH, params=params)
            batch = page.get("data") or []
            items.extend(batch)
            if len(batch) == 0:
                return items
            offset += len(batch)
