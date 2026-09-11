"""Modular input helper for the ``anthropic_directory`` input.

Writes a full snapshot of the organization directory on every run: one
``anthropic:api_keys`` event per API key and one ``anthropic:users`` event per
user, each stamped with ``snapshot_at``. Snapshots are stateless -- there is no
time window and no checkpoint; downstream searches dedup on ``snapshot_at``.

Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25 (no f-strings,
no PEP 604 unions, no match statements).
"""
import import_declare_test  # noqa: F401  isort:skip

import json
from datetime import datetime, timezone

from solnlib import conf_manager, log
from splunklib import modularinput as smi

from anthropic_client import AnthropicAdminClient

ADDON_NAME = "TA_anthropic"
SETTINGS_CONF = "ta_anthropic_settings"
ACCOUNT_CONF = "ta_anthropic_account"
INPUT_TYPE = "anthropic_directory"
API_KEYS_SOURCETYPE = "anthropic:api_keys"
USERS_SOURCETYPE = "anthropic:users"
SNAPSHOT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_BASE_URL = "https://api.anthropic.com"


def logger_for_input(input_name):
    """Per-input logger -> $SPLUNK_HOME/var/log/splunk/ta_anthropic_<input>.log."""
    return log.Logs().get_logger("{}_{}".format(ADDON_NAME.lower(), input_name))


def get_account_config(session_key, account_name):
    """Return the account stanza (api_key is decrypted by conf_manager)."""
    cfm = conf_manager.ConfManager(
        session_key,
        ADDON_NAME,
        realm="__REST_CREDENTIAL__#{}#configs/conf-{}".format(ADDON_NAME, ACCOUNT_CONF),
    )
    return cfm.get_conf(ACCOUNT_CONF).get(account_name)


def validate_input(definition):
    """No external validation beyond what globalConfig already enforces."""
    return


def stream_events(inputs, event_writer):
    # inputs.inputs is a dict keyed by "anthropic_directory://<input_name>".
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
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
            client = AnthropicAdminClient(
                api_key=account.get("api_key"),
                base_url=account.get("api_base_url") or DEFAULT_BASE_URL,
            )

            index = input_item.get("index")
            source = "{}://{}".format(INPUT_TYPE, normalized_input_name)
            # One snapshot instant per run, used for BOTH the in-body
            # ``snapshot_at`` string and the explicit event ``time``. Without an
            # explicit time Splunk falls back to timestamp heuristics and picks
            # ``created_at``/``added_at`` out of the JSON body, back-dating the
            # snapshot by months. Truncating to whole seconds keeps the epoch
            # exactly equal to the second-resolution ``snapshot_at`` string.
            snapshot_dt = datetime.now(timezone.utc).replace(microsecond=0)
            now_iso = snapshot_dt.strftime(SNAPSHOT_FORMAT)
            snapshot_time = "{:.3f}".format(snapshot_dt.timestamp())
            for sourcetype, items in (
                (API_KEYS_SOURCETYPE, client.list_api_keys()),
                (USERS_SOURCETYPE, client.list_users()),
            ):
                count = 0
                for item in items:
                    body = dict(item)
                    body["snapshot_at"] = now_iso
                    event_writer.write_event(
                        smi.Event(
                            data=json.dumps(body, ensure_ascii=False, default=str),
                            time=snapshot_time,
                            index=index,
                            sourcetype=sourcetype,
                            source=source,
                        )
                    )
                    count += 1
                log.events_ingested(
                    logger,
                    input_name,
                    sourcetype,
                    count,
                    index,
                    account=input_item.get("account"),
                )
                logger.info(
                    "Ingested {} {} events.".format(count, sourcetype)
                )

            logger.info("Directory snapshot written at {}.".format(now_iso))
            log.modular_input_end(logger, normalized_input_name)
        except Exception as e:  # never let one input kill the whole run
            log.log_exception(
                logger,
                e,
                "AnthropicDirectoryIngestionError",
                msg_before=(
                    "Exception raised while ingesting data for anthropic_directory: "
                ),
            )
