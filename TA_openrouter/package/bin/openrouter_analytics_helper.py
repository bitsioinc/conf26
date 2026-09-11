"""Modular input helper for the ``openrouter_analytics`` input.

Issues two POST /analytics/query calls per run -- the dimension cap of 2
prevents combining them -- and writes each to its own sourcetype under its own
KV Store checkpoint.

Separate checkpoints matter because truncation is per-response. A shared
checkpoint would either stall the healthy query when the other truncates, or
force it to re-ingest a window it already emitted. For the same reason,
``stream_events`` wraps each query's collection in its own try/except: a
transient failure on one query (a network blip, an ``OpenRouterAPIError``)
is logged and the loop moves on to the next query, rather than aborting it
for the interval. Only genuinely per-input failures -- session key, account
config, client/checkpointer construction -- abort the whole input; those
happen before any query can run at all, so isolating them per-query would be
meaningless.

``logger_for_input()`` itself runs *inside* the per-input try, not before
it: a failure constructing the per-input logger used to raise outside every
try/except in the ``for input_name, input_item in inputs.inputs.items()``
loop, aborting ``stream_events`` entirely and skipping every remaining
configured input for the run -- not just the one being set up. The except
handler falls back to a module-level logger (named after the add-on only, so
it never depends on the input name that may not be safely available)
whenever the per-input logger was never created, so the failure is still
reported instead of silently swallowed. Same shape as
``openrouter_keys_helper.py`` for consistency between the two modules.

The two queries do NOT share a time-bucket column. Live recording (see
TA_openrouter/docs/schema-verification.md) verified that
``[api_key_id, model]`` rows carry their bucket in ``date__hour`` while
``[model, provider]`` rows carry it in ``created_at__hour``. Both differ from
``flatten_analytics``/``max_bucket``'s default of ``"date"``. Each entry in
QUERIES therefore carries its own explicit ``time_field``, which
``collect_query`` passes through to both functions -- auto-detecting the
column was considered and rejected. Getting this wrong is silent: rows are
simply skipped with no error, and the input ingests zero events forever.

Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25 (no f-strings,
no PEP 604 unions, no match statements).
"""
try:
    import import_declare_test  # noqa: F401  isort:skip

    from solnlib import conf_manager, log
    from solnlib.modular_input import checkpointer
    from splunklib import modularinput as smi
except ImportError:  # unit tests run without the Splunk runtime
    conf_manager = log = checkpointer = smi = None

import json
from datetime import datetime, timezone

from openrouter_client import OpenRouterClient
from openrouter_transform import (
    compute_window, flatten_analytics, is_truncated, max_bucket, row_count,
)

ADDON_NAME = "TA_openrouter"
SETTINGS_CONF = "ta_openrouter_settings"
ACCOUNT_CONF = "ta_openrouter_account"
CHECKPOINT_COLLECTION = "TA_openrouter_checkpoints"
INPUT_TYPE = "openrouter_analytics"
GRANULARITY = "hour"
DEFAULT_BACKFILL_DAYS = 7
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

#: Additive metrics only. Every metric carrying is_rate=true in
#: /analytics/meta is excluded, because summing a rate across time buckets or
#: dimension values produces a meaningless number and Splunk will do it
#: silently.
METRICS = [
    "request_count",
    "total_usage",
    "tokens_total",
    "tokens_prompt",
    "tokens_completion",
    "reasoning_tokens",
    "cached_tokens",
    "byok_usage",
]

#: Two queries because `dimensions` caps at 2 and all three of api_key_id,
#: model, and provider are needed. `model` is the axis that appears in both
#: entries below -- it's what lets a reader correlate the Model Mix and
#: Provider Routing dashboards by eye (the same model name shows up in
#: both). No shipped panel or saved search actually joins the two
#: sourcetypes together.
#:
#: `time_field` is per-query and VERIFIED against a live recording (see
#: schema-verification.md) -- it is NOT the same column name for both
#: queries, and neither matches flatten_analytics/max_bucket's "date"
#: default. Getting this wrong silently drops every row for that query.
QUERIES = (
    {
        "dimensions": ["api_key_id", "model"],
        "metrics": METRICS,
        "sourcetype": "openrouter:analytics",
        "checkpoint_suffix": "analytics_by_key_model",
        "time_field": "date__hour",
    },
    {
        "dimensions": ["model", "provider"],
        "metrics": METRICS,
        "sourcetype": "openrouter:providers",
        "checkpoint_suffix": "analytics_by_model_provider",
        "time_field": "created_at__hour",
    },
)


def logger_for_input(input_name):
    """Per-input logger -> $SPLUNK_HOME/var/log/splunk/ta_openrouter_<input>.log."""
    return log.Logs().get_logger("{}_{}".format(ADDON_NAME.lower(), input_name))


_FALLBACK_LOGGER = None


def _fallback_logger():
    """Logger to use when ``logger_for_input`` itself failed for this input.

    Named after the add-on only -- it deliberately does not depend on the
    input name, which is exactly the value that may not be safely available
    if logger construction is what raised. Cached at module level (not
    per-input) so repeated failures across a run share one logger object.
    """
    global _FALLBACK_LOGGER
    if _FALLBACK_LOGGER is None:
        _FALLBACK_LOGGER = log.Logs().get_logger(ADDON_NAME.lower())
    return _FALLBACK_LOGGER


def get_account_config(session_key, account_name):
    """Return the account stanza (api_key is decrypted by conf_manager)."""
    cfm = conf_manager.ConfManager(
        session_key,
        ADDON_NAME,
        realm="__REST_CREDENTIAL__#{}#configs/conf-{}".format(ADDON_NAME, ACCOUNT_CONF),
    )
    return cfm.get_conf(ACCOUNT_CONF).get(account_name)


def _int_or_default(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def collect_query(client, query, checkpoint, now, backfill_days,
                   diagnostics=None):
    """Run one query. Returns (events, next_checkpoint).

    ``next_checkpoint`` is None whenever the checkpoint must not move: no
    complete bucket pending, an empty window, or a truncated response. On
    truncation the events are discarded too -- emitting a partial window while
    holding the checkpoint would duplicate those rows on the next run. This
    guard is unconditional and does not depend on ``diagnostics`` in any way.

    ``query["time_field"]`` is passed to both ``flatten_analytics`` and
    ``max_bucket``. Passing it to only one would desync the emitted events
    from the checkpoint that describes them.

    All three ``([], None)``-returning conditions above look identical to a
    caller that only looks at the return value -- but truncation means rows
    are being silently withheld, forever, until the window shrinks, while
    the other two are unremarkable. ``diagnostics``, when a dict is passed,
    is updated in place with a ``"reason"`` key so ``stream_events`` can log
    truncation at WARN instead of the routine INFO:

    * ``"no_window"`` -- no complete bucket yet; no API call was made.
    * ``"empty"``     -- the window was queried and genuinely had 0 rows.
    * ``"truncated"`` -- the server capped the result set; also sets
      ``"window"`` (the ``(start, end)`` tuple queried) and ``"row_count"``
      (rows the server actually returned, capped at the request limit).
    * ``"ok"``        -- events were returned normally.

    Kept as an optional keyword argument, out of the return tuple itself, so
    every existing caller that unpacks ``events, checkpoint =
    collect_query(...)`` with five positional arguments keeps working
    completely unmodified.
    """
    if diagnostics is None:
        diagnostics = {}
    window = compute_window(checkpoint, now, backfill_days, granularity=GRANULARITY)
    if window is None:
        diagnostics["reason"] = "no_window"
        return [], None
    start, end = window
    payload = client.query_analytics(
        query["metrics"], query["dimensions"], GRANULARITY, start, end,
    )
    if is_truncated(payload):
        diagnostics["reason"] = "truncated"
        diagnostics["window"] = (start, end)
        diagnostics["row_count"] = row_count(payload)
        return [], None
    events = flatten_analytics(payload, query["dimensions"],
                                time_field=query["time_field"])
    if not events:
        diagnostics["reason"] = "empty"
        return [], None
    diagnostics["reason"] = "ok"
    return events, max_bucket(payload, time_field=query["time_field"])


def validate_input(definition):
    """No external validation beyond what globalConfig already enforces."""
    return


def stream_events(inputs, event_writer):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        # logger starts unset and is created *inside* the try below. If
        # logger_for_input() itself raises, the except handler below still
        # needs something to report through -- that's what _fallback_logger()
        # is for. Per-input try/except: a failure collecting this input
        # (including failing to construct its own logger) must not prevent
        # any other configured openrouter_analytics input from running this
        # interval.
        logger = None
        try:
            logger = logger_for_input(normalized_input_name)
            session_key = inputs.metadata["session_key"]
            log_level = conf_manager.get_log_level(
                logger=logger,
                session_key=session_key,
                app_name=ADDON_NAME,
                conf_name=SETTINGS_CONF,
            )
            logger.setLevel(log_level)
            log.modular_input_start(logger, normalized_input_name)

            account = get_account_config(session_key, input_item.get("account"))
            client = OpenRouterClient(
                api_key=account.get("api_key"),
                base_url=account.get("api_base_url") or DEFAULT_BASE_URL,
            )
            ckpt = checkpointer.KVStoreCheckpointer(
                CHECKPOINT_COLLECTION, session_key, ADDON_NAME
            )
            backfill_days = _int_or_default(
                input_item.get("backfill_days"), DEFAULT_BACKFILL_DAYS
            )
            index = input_item.get("index")
            source = "{}://{}".format(INPUT_TYPE, normalized_input_name)
            now = datetime.now(timezone.utc)

            for query in QUERIES:
                ckpt_key = "{}_{}".format(
                    normalized_input_name, query["checkpoint_suffix"]
                )
                # Each query gets its own try/except so a transient failure
                # on one (a network blip, an OpenRouterAPIError) cannot abort
                # its sibling for this interval -- that would leave a
                # perfectly healthy query's checkpoint stalled for no reason
                # related to it, which is exactly the cross-query
                # interference separate checkpoints exist to avoid. The
                # checkpoint update for the failing query is unreachable once
                # an exception is raised above it, so a failing query can
                # never advance its own checkpoint either way.
                try:
                    diagnostics = {}
                    events, next_ckpt = collect_query(
                        client, query, ckpt.get(ckpt_key), now, backfill_days,
                        diagnostics=diagnostics,
                    )
                    for event in events:
                        event_writer.write_event(
                            smi.Event(
                                data=json.dumps(event["data"]),
                                time=event["time"],
                                index=index,
                                sourcetype=query["sourcetype"],
                                source=source,
                            )
                        )
                    if next_ckpt:
                        ckpt.update(ckpt_key, next_ckpt)
                    # Truncation, an empty window, and a genuinely-zero-row
                    # window are otherwise indistinguishable: all three
                    # return events=[] and next_ckpt=None. Truncation is the
                    # one that needs a WARN -- it means rows are being
                    # silently withheld, forever, until an operator manually
                    # advances the checkpoint (the input's interval has no
                    # effect on the window and cannot shrink it). Bisecting
                    # the window automatically was considered and declined;
                    # this WARN is the operator's signal to intervene
                    # manually. See TA_openrouter/docs (operations runbook)
                    # for the remedy.
                    if diagnostics.get("reason") == "truncated":
                        logger.warning(
                            "Truncated response for input={} sourcetype={} "
                            "window={} row_count={}; checkpoint held, no "
                            "events ingested this run. This window has more "
                            "rows than the server will return in one "
                            "response; this will recur every run until an "
                            "operator manually advances the checkpoint -- "
                            "see the Truncation section in "
                            "TA_openrouter/docs/operations.md.".format(
                                normalized_input_name, query["sourcetype"],
                                diagnostics.get("window"),
                                diagnostics.get("row_count"),
                            )
                        )
                    logger.info(
                        "Ingested {} {} events; checkpoint={}".format(
                            len(events), query["sourcetype"], next_ckpt
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log.log_exception(
                        logger, exc, "OpenRouterAnalyticsQueryError",
                        msg_before=(
                            "Failed to collect {} for input={} "
                            "checkpoint_suffix={}: ".format(
                                query["sourcetype"], normalized_input_name,
                                query["checkpoint_suffix"],
                            )
                        ),
                    )

            log.modular_input_end(logger, normalized_input_name)
        except Exception as exc:  # noqa: BLE001
            # logger may still be None here (logger_for_input() itself
            # raised, above) -- fall back rather than let a second
            # exception (from calling a method on None) replace the real
            # one and escape this try/except entirely.
            log.log_exception(
                logger or _fallback_logger(), exc, "OpenRouterAnalyticsError",
                msg_before="Failed to collect OpenRouter analytics for "
                           "input={}: ".format(normalized_input_name),
            )
