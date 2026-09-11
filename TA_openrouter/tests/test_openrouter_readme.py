"""Pin README.md's Build/Installation instructions against the real files
they describe.

This exists because the install path documented in README.md previously
dead-ended: step 1 said to install `dist/TA_openrouter-0.1.0.tar.gz`, but
`dist/*.tar.gz` is git-ignored and nothing in README.md or docs/ said how to
produce it. That is one of four false claims this documentation set has
already shipped and had reach review (a remedy that did not work, a log
quote that no longer existed, a join that never existed, and this dead-end
install path) -- this module exists so a fifth cannot land silently.

Each check below ties a specific sentence in the Build section back to a
real file/command, rather than trusting the prose in isolation.
"""
import stat
from pathlib import Path

TA_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = TA_ROOT.parent

README = TA_ROOT / "README.md"
BUILD_SCRIPT = TA_ROOT / "scripts" / "build.sh"
REQUIREMENTS_DEV = REPO_ROOT / "requirements-dev.txt"
LIB_REQUIREMENTS = TA_ROOT / "package" / "lib" / "requirements.txt"
GITIGNORE = REPO_ROOT / ".gitignore"


def readme_text():
    return README.read_text()


def test_build_script_exists_and_is_executable():
    # README's Build section tells the reader to run this path directly
    # (`TA_openrouter/scripts/build.sh`) from the repo root; it must exist
    # and be executable for that to work.
    assert BUILD_SCRIPT.is_file()
    mode = BUILD_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/build.sh must be executable"


def test_requirements_dev_txt_is_at_the_repo_root_not_under_ta_openrouter():
    # README documents a single shared repo-root requirements-dev.txt, not a
    # TA_openrouter-local one -- pin both halves of that claim.
    assert REQUIREMENTS_DEV.is_file()
    assert not (TA_ROOT / "requirements-dev.txt").exists(), (
        "a TA_openrouter-local requirements-dev.txt appeared -- update "
        "README.md's Build section, which currently documents only the "
        "repo-root one")


def test_requirements_dev_txt_provides_ucc_gen_but_not_slim():
    # This is the load-bearing fact behind the README's extra
    # `pip install splunk-packaging-toolkit` step: requirements-dev.txt
    # supplies ucc-gen (via splunk-add-on-ucc-framework) but not slim. If
    # this ever changes, the README's extra step becomes redundant (harmless
    # to leave, but worth knowing) -- if slim's source package disappears
    # from here in the other direction, the README step becomes load-bearing
    # in a new way. Either way, the README claim needs re-checking against
    # this file, so pin what it says today.
    text = REQUIREMENTS_DEV.read_text()
    assert "splunk-add-on-ucc-framework" in text
    assert "splunk-packaging-toolkit" not in text, (
        "requirements-dev.txt now installs slim's package directly -- "
        "README.md's separate `pip install splunk-packaging-toolkit` step "
        "is redundant and should be removed")


def test_lib_requirements_txt_exists_where_readme_says():
    # README's TA_OPENROUTER_PYTHON39 explanation says scripts/build.sh
    # vendors wheels for this specific file.
    assert LIB_REQUIREMENTS.is_file()


def test_gitignore_actually_ignores_the_dist_artifact():
    # README's Build section opens by explaining *why* dist/ doesn't exist
    # in a fresh clone -- pin that dist/*.tar.gz really is git-ignored so
    # that explanation cannot silently go stale.
    patterns = GITIGNORE.read_text().splitlines()
    assert "*.tar.gz" in patterns


def test_readme_documents_the_full_build_toolchain():
    text = readme_text()
    for expected in (
        "## Build",
        "python3 -m venv .venv",
        "pip install -r requirements-dev.txt",
        "splunk-packaging-toolkit",
        "TA_OPENROUTER_PYTHON39",
        "TA_openrouter/scripts/build.sh",
        "dist/TA_openrouter-",
    ):
        assert expected in text, "README.md Build section is missing: {!r}".format(expected)


def test_build_section_appears_before_installation_section():
    text = readme_text()
    build_pos = text.index("## Build")
    install_pos = text.index("## Installation")
    assert build_pos < install_pos, (
        "the Build section must come before Installation -- installation "
        "step 1 depends on the artifact Build produces")


def test_installation_step_one_points_at_the_build_output_not_a_dead_end():
    text = readme_text()
    install_section = text[text.index("## Installation"):text.index("## Inputs")]
    assert "dist/TA_openrouter-0.1.0.tar.gz" in install_section
    # Regression guard for the original defect: step 1 must not read as a
    # bare, unexplained path -- it must point back at the section that
    # produces it.
    assert "built above" in install_section
