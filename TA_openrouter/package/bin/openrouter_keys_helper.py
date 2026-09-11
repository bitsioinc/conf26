"""Modular input helper for the ``openrouter_keys`` input.

Snapshots the API key roster. There is no checkpoint: the roster is small and
slowly changing, so every run emits the full list stamped with ``snapshot_at``
and downstream searches dedup on it.

Only the default workspace's keys are returned; multi-workspace enumeration
via workspace_id is out of scope for v0.1.0.

There is only one collection per input here (unlike openrouter_analytics_helper's
two queries), so there is no per-unit-of-work loop to isolate internally.
``stream_events`` still isolates failures at the *input* level: the try/except
lives inside the ``for input_name, input_item in inputs.inputs.items()`` loop,
so a failure collecting one configured input's roster (bad account config, an
OpenRouterAPIError, a runaway-pagination guard trip) is logged and the loop
moves on to the next configured input, rather than aborting every other
openrouter_keys input for the interval.

``logger_for_input()`` itself now runs *inside* that per-input try, not
before it: a failure constructing the per-input logger used to raise outside
every try/except in the loop, aborting ``stream_events`` entirely and
skipping every remaining configured input for the run -- not just the one
being set up. The except handler falls back to a module-level logger (named
after the add-on only, so it never depends on the input name that may not
be safely available) whenever the per-input logger was never created, so the
failure is still reported instead of silently swallowed.

Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25.
"""
try:
    import import_declare_test  # noqa: F401  isort:skip

    from solnlib import conf_manager, log
    from splunklib import modularinput as smi
except ImportError:  # unit tests run without the Splunk runtime
    conf_manager = log = smi = None

import json
from datetime import datetime, timezone

from openrouter_client import OpenRouterClient
from openrouter_transform import RFC3339, flatten_keys

ADDON_NAME = "TA_openrouter"
SETTINGS_CONF = "ta_openrouter_settings"
ACCOUNT_CONF = "ta_openrouter_account"
SOURCETYPE = "openrouter:keys"
INPUT_TYPE = "openrouter_keys"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def logger_for_input(input_name):
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
    cfm = conf_manager.ConfManager(
        session_key,
        ADDON_NAME,
        realm="__REST_CREDENTIAL__#{}#configs/conf-{}".format(ADDON_NAME, ACCOUNT_CONF),
    )
    return cfm.get_conf(ACCOUNT_CONF).get(account_name)


def collect_keys(client, now):
    """Return one event per API key, stamped with a shared snapshot time."""
    snapshot_at = now.astimezone(timezone.utc).strftime(RFC3339)
    return flatten_keys(client.list_keys(include_disabled=True), snapshot_at)


def validate_input(definition):
    return


def stream_events(inputs, event_writer):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        # logger starts unset and is created *inside* the try below. If
        # logger_for_input() itself raises, the except handler below still
        # needs something to report through -- that's what _fallback_logger()
        # is for. Per-input try/except: a failure collecting this input's
        # roster (including failing to construct its own logger) must not
        # prevent any other configured openrouter_keys input from running
        # its own collection this interval.
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
            events = collect_keys(client, datetime.now(timezone.utc))
            source = "{}://{}".format(INPUT_TYPE, normalized_input_name)
            for event in events:
                event_writer.write_event(
                    smi.Event(
                        data=json.dumps(event["data"]),
                        time=event["time"],
                        index=input_item.get("index"),
                        sourcetype=SOURCETYPE,
                        source=source,
                    )
                )
            logger.info("Ingested {} key events".format(len(events)))
            log.modular_input_end(logger, normalized_input_name)
        except Exception as exc:  # noqa: BLE001
            # logger may still be None here (logger_for_input() itself
            # raised, above) -- fall back rather than let a second
            # exception (from calling a method on None) replace the real
            # one and escape this try/except entirely.
            log.log_exception(
                logger or _fallback_logger(), exc, "OpenRouterKeysError",
                msg_before="Failed to collect OpenRouter API keys for "
                           "input={}: ".format(normalized_input_name),
            )
