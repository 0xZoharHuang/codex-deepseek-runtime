# Third-party notices

This distribution includes a small source patch against OpenAI Codex, Apache-2.0,
commit `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`. The model instruction template in
`catalog.json` derives from that project. OpenAI's LICENSE and NOTICE are retained
in `licenses/`; package-native notices retain the matching upstream copies.

The CLI is compiled from the patched source. The Code Mode Host uses the matching
hash-verified official release. Ripgrep 15.2.0 retains its upstream MIT/Unlicense
and COPYING files. Native external crate attribution, upstream notice files and
pinned canonical SPDX texts are included in the native package's `licenses/rust`.
Its index intentionally covers a superset of platform, build and test dependencies.
`native_licenses.py` records declared license expressions without inventing authors
or claiming that upstream omitted notice files were present.

Linux packages additionally include the native Codex-built bubblewrap resource.
Its original GNU Library General Public License text, exact vendored sources and
build provenance accompany it under `licenses/bubblewrap-*`. The complete pinned
upstream source archive and checksum are recorded there. The resource is built and
stripped before its checksum is embedded in the native CLI, following upstream's
release process; the adapter does not replace the sandbox implementation.

Python libraries are installed from exact version/hash locks and retain their own
installed package licenses. Real-work fixtures identify public upstream projects
and commits; test snapshots, private OAuth credentials and raw user traces are not
part of the compact source release. SPDX texts come from the pinned
`spdx/license-list-data` revision recorded in `native/license-data.json`.

The bundled document browser uses the official Model Context Protocol Python SDK
(`mcp` and `mcp-types`, MIT), Beautiful Soup (MIT), Soup Sieve (MIT), pypdf (BSD-3-Clause),
TOML Kit (MIT) and truststore (MIT). Structured-output verification uses jsonschema
and referencing (MIT). All transitive Python versions and distribution hashes are
pinned in `requirements.lock`; their distribution metadata and original license
files remain in the immutable installed environment. These are installed
dependencies, not copied source incorporated into the native Codex binary.

The source patch and build recipes are available alongside this distribution so
native changes can be independently inspected. Public upstream contributions are
separately labeled; local patches do not imply upstream endorsement.

Windows console entrypoints use the public ScriptMaker API of distlib 0.4.4
(Python Software Foundation license). Its original license is retained in
`licenses/distlib-LICENSE.txt` and its wheel is pinned by SHA-256 in the lock.
The standard distlib executable launcher directly starts the isolated Python
entry script; it does not embed or replace the Codex agent runtime.
