# Third-party notices (internal; required for legal review)

## rtk — https://github.com/rtk-ai/rtk
- License: Apache-2.0 (root `LICENSE`). The `engine/` and `proxy/` directories are BUSL-1.1 and were not read,
  copied or used.
- Commit read: f16becacd2f40616839333b3e0e281e40a0bc823 (2026-10-15).
- Informed module: `trim` (`src/ai_usage_kit/trim/filters/`). Behavior of these filters was ported as new
  Python code: `src/cmds/python/pytest_cmd.rs`, `src/filters/uv-sync.toml`, `src/cmds/js/npm_cmd.rs`,
  `src/cmds/jvm/gradlew_cmd.rs` (build filter and task detection), `text/src/lib.rs` (truncate, strip_ansi).
- Test cases ported from the same files. Copied verbatim: `tests/fixtures/gradlew_build_raw.txt` →
  `tests/trim/fixtures/gradle_build_raw.txt` (Apache-2.0, Copyright the rtk authors).
- Deliberate differences are listed in `docs/trim.md`.

## caveman — https://github.com/JuliusBrussee/caveman
- License: Apache-2.0 for the whole repository from 3.0.0 (root `LICENSE`, `LICENSING.md`; MIT text kept in
  `LICENSE-MIT` for earlier contributions). `engine/pixel/` embeds third-party fonts (OFL/GPL with exception); it
  was not read or used.
- Commit read: d262ab3882952a59312ff3537cd86d894523c5ab. Only `skills/caveman/SKILL.md` was read.
- Informed: `src/ai_usage_kit/rules/texts.py` (`CONCISE`). Written in our own words; a few short functional
  phrases (for example the list of things kept verbatim) are necessarily similar. Apache-2.0 text: see the
  reference's root `LICENSE`.
- Deliberate differences: normal grammar is kept (the reference allows dropping articles and fragments); no
  modes, status commands or activation phrases.

## ponytail — https://github.com/DietrichGebert/ponytail
- License: MIT. Copyright (c) 2026 DietrichGebert. The MIT notice is reproduced below because a few short
  phrases in `MINIMAL` are close to the reference's wording.
- Commit read: 742e0abae1501e12d1254a81d05f16bba10d08c1. Only `skills/ponytail/SKILL.md` was read.
- Informed: `src/ai_usage_kit/rules/texts.py` (`MINIMAL`), following its "lite" level. Written in our own
  words; some short functional phrases (the order of options to try, carrying error handling along when moving
  code, the closing line about what was left out and any risk) are close to the reference's wording.
- Deliberate differences: no levels, persona or activation phrases; "never cut validation, security, error
  handling or tests" is stated explicitly.

### MIT License (ponytail)

```
MIT License

Copyright (c) 2026 DietrichGebert

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
