"""Guards Fix 1 of the 93-agent audit: every input service in
globalConfig.json must declare an ``inputHelperModule`` naming a module
that actually exists under ``package/bin/``.

Without ``inputHelperModule``, UCC's ``ucc-gen`` (see
``splunk_add_on_ucc_framework/commands/build.py:_add_modular_input``)
silently emits a *self-contained stub* input script instead of wiring the
real helper: it dumps the input's own stanza settings to a sourcetype named
after the input itself (``openrouter_analytics``, underscore -- matching
none of this add-on's ``openrouter:analytics`` props stanzas), never calls
OpenRouter, and never checkpoints. ``openrouter_client.py``,
``openrouter_transform.py``, and both helper modules would ship as dead
code. Critically, this produces 0 errors from both ``slim validate`` and
AppInspect precert -- nothing in the release toolchain catches it, which is
exactly why this needs its own guard.

``TA_openrouter/scripts/build.sh`` does not exist yet (a separate task
owns it); this test guards the source config regardless of when build.sh
lands, and independently of any real ``ucc-gen`` invocation.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLOBAL_CONFIG = ROOT / "globalConfig.json"
BIN_DIR = ROOT / "package" / "bin"


def _load_config():
    return json.loads(GLOBAL_CONFIG.read_text())


def _services():
    return _load_config()["pages"]["inputs"]["services"]


def test_global_config_declares_at_least_two_input_services():
    # Sanity check for the fixture itself: if this ever drops to zero the
    # tests below would vacuously pass.
    services = _services()
    assert len(services) >= 2
    assert {s["name"] for s in services} == {"openrouter_analytics", "openrouter_keys"}


def test_every_input_service_declares_input_helper_module():
    for service in _services():
        assert "inputHelperModule" in service, (
            "service {!r} has no inputHelperModule -- ucc-gen will emit a "
            "self-contained stub that never calls the real helper module, "
            "never contacts OpenRouter, and never checkpoints".format(
                service.get("name")
            )
        )
        assert service["inputHelperModule"], (
            "service {!r} declares an empty inputHelperModule".format(
                service.get("name")
            )
        )


def test_input_helper_module_names_a_file_that_exists_under_package_bin():
    for service in _services():
        module_name = service["inputHelperModule"]
        module_path = BIN_DIR / (module_name + ".py")
        assert module_path.is_file(), (
            "service {!r} declares inputHelperModule={!r}, but {} does not "
            "exist -- ucc-gen falls back to FileNotFoundError handling and "
            "silently skips arity checks for a helper that isn't really "
            "there".format(service["name"], module_name, module_path)
        )


def test_analytics_service_wires_the_analytics_helper():
    by_name = {s["name"]: s for s in _services()}
    assert (
        by_name["openrouter_analytics"]["inputHelperModule"]
        == "openrouter_analytics_helper"
    )


def test_keys_service_wires_the_keys_helper():
    by_name = {s["name"]: s for s in _services()}
    assert by_name["openrouter_keys"]["inputHelperModule"] == "openrouter_keys_helper"


def test_helper_modules_satisfy_uccs_arity_contract():
    # ucc-gen (build.py) requires module-level `def stream_events(inputs,
    # event_writer)` (2 args) and `def validate_input(definition)` (1 arg)
    # -- or the same with a leading `self` if the function is a bound
    # method. Verified here via ast so a signature drift in either helper
    # fails this test instead of only failing a real `ucc-gen` build.
    import ast

    expected_args = {"stream_events": 2, "validate_input": 1}
    for service in _services():
        module_name = service["inputHelperModule"]
        module_path = BIN_DIR / (module_name + ".py")
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in expected_args:
                found[node.name] = len(node.args.args)
        for func_name, expected in expected_args.items():
            assert func_name in found, (
                "{} has no top-level def {}(...)".format(module_path, func_name)
            )
            actual = found[func_name]
            assert actual in (expected, expected + 1), (
                "{} in {} has {} args; ucc-gen expects {} (or {} with a "
                "leading self)".format(
                    func_name, module_path, actual, expected, expected + 1
                )
            )
