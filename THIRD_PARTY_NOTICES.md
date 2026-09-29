# Third-party notices

This repository redistributes files from the ABCD (Action-Based Conversations Dataset) project. Their license is reproduced verbatim below.

## ABCD — Action-Based Conversations Dataset

- **Upstream**: <https://github.com/asappresearch/abcd>
- **License**: MIT (verified against `asappresearch/abcd`'s `LICENSE` on 2026-09-28 via the GitHub Licenses API)
- **Files redistributed in this repo**:
  - `data/abcd/abcd_sample.json` — three sample conversations
  - `data/abcd/guidelines.json` — per-flow agent guidelines used to score the `correctness` signal
  - `data/abcd/kb.json` — canonical subflow slugs, used to validate intent labels
- **Files intentionally NOT redistributed** (drop them in yourself, see `README.md`):
  - `abcd_v1.1.json.gz` — the full ~10K-conversation dump

### Upstream license text (`asappresearch/abcd/LICENSE`)

```
MIT License

Copyright (c) 2021 ASAPP Research

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
