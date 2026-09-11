"""Modular input helper for the ``anthropic_usage`` input.

Pulls the Anthropic Admin API usage report (``bucket_width=1h``) and writes one
``anthropic:usage`` event per (bucket, result) row. Resumes from a KV Store
checkpoint so overlapping/duplicate buckets are not re-ingested.

Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25 (no f-strings,
no PEP 604 unions, no match statements).
"""
import import_declare_test  # noqa: F401  isort:skip

import json
from datetime import datetime, timezone

from solnlib import conf_manager, log
from solnlib.modular_input import checkpointer
from splunklib import modularinput as smi

from anthropic_client import AnthropicAdminClient
from anthropic_transform import compute_window, flatten_usage, max_ending_at

ADDON_NAME = "TA_anthropic"
SETTINGS_CONF = "ta_anthropic_settings"
ACCOUNT_CONF = "ta_anthropic_account"
CHECKPOINT_COLLECTION = "TA_anthropic_checkpoints"
CHECKPOINT_PREFIX = "usage_"
SOURCETYPE = "anthropic:usage"
INPUT_TYPE = "anthropic_usage"
BUCKET_WIDTH = "1h"
GROUP_BY = ["model", "workspace_id", "api_key_id", "service_tier"]
DEFAULT_BACKFILL_DAYS = 7
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


def _int_or_default(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_input(definition):
    """No external validation beyond what globalConfig already enforces."""
    return


def stream_events(inputs, event_writer):
    # inputs.inputs is a dict keyed by "anthropic_usage://<input_name>".
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

            ckpt = checkpointer.KVStoreCheckpointer(
                CHECKPOINT_COLLECTION, session_key, ADDON_NAME
            )
            ckpt_key = "{}{}".format(CHECKPOINT_PREFIX, normalized_input_name)
            window = compute_window(
                ckpt.get(ckpt_key),
                datetime.now(timezone.utc),
                backfill_days=_int_or_default(
                    input_item.get("backfill_days"), DEFAULT_BACKFILL_DAYS
                ),
                bucket_width=BUCKET_WIDTH,
            )
            if window is None:
                logger.info("No complete bucket pending; skipping run.")
                log.modular_input_end(logger, normalized_input_name)
                continue

            starting_at, ending_at = window
            logger.info(
                "Collecting usage from {} to {}".format(starting_at, ending_at)
            )
            index = input_item.get("index")
            source = "{}://{}".format(INPUT_TYPE, normalized_input_name)
            pages = []
            count = 0
            for page in client.iter_usage_report(
                starting_at, ending_at, bucket_width=BUCKET_WIDTH, group_by=GROUP_BY
            ):
                pages.append(page)
                for event in flatten_usage(page):
                    event_writer.write_event(
                        smi.Event(
                            data=json.dumps(
                                event["data"], ensure_ascii=False, default=str
                            ),
                            time="{:.3f}".format(event["time"]),
                            index=index,
                            sourcetype=SOURCETYPE,
                            source=source,
                        )
                    )
                    count += 1

            new_ckpt = max_ending_at(pages)
            if new_ckpt:
                ckpt.update(ckpt_key, new_ckpt)

            log.events_ingested(
                logger,
                input_name,
                SOURCETYPE,
                count,
                index,
                account=input_item.get("account"),
            )
            logger.info(
                "Ingested {} usage events; checkpoint={}".format(count, new_ckpt)
            )
            log.modular_input_end(logger, normalized_input_name)
        except Exception as e:  # never let one input kill the whole run
            log.log_exception(
                logger,
                e,
                "AnthropicUsageIngestionError",
                msg_before="Exception raised while ingesting data for anthropic_usage: ",
            )
