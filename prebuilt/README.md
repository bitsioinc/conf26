# Prebuilt packages

Installable packages for both add-ons, so you can try them in Splunk without
setting up a build toolchain. Version 0.1.0.

| File | Size | Add-on |
| --- | --- | --- |
| `TA_anthropic-0.1.0.tar.gz` | 18 MB | [Anthropic Add-on](../TA_anthropic/README.md) |
| `TA_openrouter-0.1.0.tar.gz` | 15 MB | [OpenRouter Add-on](../TA_openrouter/README.md) |

Verify before installing:

```bash
shasum -a 256 -c SHA256SUMS
```

## Install

Splunk Web → **Apps → Manage Apps → Install app from file** → upload the
`.tar.gz` → restart Splunk. Or extract straight into `$SPLUNK_HOME/etc/apps/`
and restart.

Then follow the **Installation** section of that add-on's README to configure an
account and create inputs. Neither add-on needs a vendor API key to try — both
ship an offline mock of their upstream API; see the repository README.

## Read this before installing on Linux

**These packages were built on macOS.** `ucc-gen` vendors third-party
dependencies into `lib/` at build time, and four of the vendored files are
native extensions compiled as Mach-O (x86_64 + arm64):

```
lib/charset_normalizer/md.cpython-39-darwin.so
lib/charset_normalizer/cd.cpython-39-darwin.so
lib/google/_upb/_message.abi3.so
lib/grpc/_cython/cygrpc.cpython-39-darwin.so
```

All four are transitive dependencies — `grpc` and `google.protobuf` arrive via
`opentelemetry-exporter-otlp-proto-grpc`, which comes in with `solnlib` /
`splunktaucclib`. **Neither add-on's own code imports any of them.** Importing
the full modular-input dependency set under Splunk's own CPython 3.9.25 pulls in
none of `grpc`, `google`, or `opentelemetry`, and `charset_normalizer` resolves
to its pure-Python `md.py` rather than the compiled extension.

So these packages are *expected* to work on Linux, because nothing on the
execution path loads a Mach-O file. **That has not been verified on Linux**, and
this documentation will not claim it has. For anything beyond a quick trial —
and for any production install — build on your target platform instead:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install splunk-packaging-toolkit

./scripts/build.sh                # TA_anthropic
TA_openrouter/scripts/build.sh    # TA_openrouter
```

`TA_openrouter/scripts/build.sh` pins the interpreter to Python 3.9 and refuses
to build without one, so it cannot silently vendor wheels for the wrong runtime.
`scripts/build.sh` does not pin it — see the caveats in
[`TA_anthropic/README.md`](../TA_anthropic/README.md).

## What was verified about these two files

Both packages, as committed:

- `slim validate` → 0 errors (TA_anthropic reports 9 benign UCC warnings about
  `python.version` and `supported_themes`, which SLIM's spec does not know)
- 0 `__pycache__`, `.pyc`, `.DS_Store` or `.git` entries
- `app.manifest` declares author `bitsIO Inc`, license `Apache-2.0`,
  `developmentStatus` `Development/Beta`
- a real 11,358-byte `LICENSES/LICENSE.txt` and real `README.txt` release notes
- no account identifier, key hash, credential, personal name, email address or
  local filesystem path anywhere in either payload
- all nine add-on modules compile under Splunk's CPython 3.9.25

## Why they are 18 MB and 15 MB

Almost entirely the vendored `grpc` tree — 38 MB uncompressed — which arrives
transitively and is never imported. `package/lib/exclude.txt` is `ucc-gen`'s
sanctioned mechanism for dropping vendored packages, and is the lever to pull if
you want a smaller artifact. Doing so needs verification against a running
Splunk instance, so it has deliberately not been done here.
