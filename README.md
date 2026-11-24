> 🚧 **Status: active development.** It works for the author; interfaces may change.

# karincaai-icerik-studyosu

[Türkçe](README.tr.md)

An offline, standard-library-only Python core for a content-writing workflow ("Content Studio"): a local SQLite store for **sources** and **drafts**, strict revision control, an editorial checker, and two prompt builders that prepare text you can paste into a model yourself. It is a narrow code selection, **not** a full application: there is no UI, server, automatic URL fetching, model provider, media handling or posting to social networks. It never calls a model.

It exists to keep the boring, easy-to-get-wrong parts of AI-assisted writing explicit and testable: which sources belong to which project, which draft revision was reviewed, and what an AI prompt is allowed to contain.

## How it works

- `studio/store.py` — `StudioStore`: sources (manual or imported), drafts tied to one primary source and optional project sources, revision numbers (an update on a stale revision raises `RevisionConflict`), history and restore, copy approval, jobs and proposals.
- `studio/source_evidence.py` — URL and evidence validation. Source URLs with user info, a port that is invalid, a query string or a fragment are rejected.
- `studio/editorial.py` — reader brief, angle suggestions, claim-map checks and a deterministic review of a draft.
- `studio/generator.py` — `build_prompt` (draft + sources + your instruction → text) and `parse_generated_draft` (strict parser for a model's JSON answer).
- `studio/content_standard.py` + `content_standard.v1.json`, `editorial_policy.json` — the content standard and editorial policy the checks read.
- `studio/text_budget.py` — fails closed (no approximate counter is invented).

## Quick start

Requirements: Python 3.10 or newer, standard library only (tested with 3.12).

```python
from pathlib import Path
from studio.store import StudioStore
from studio.generator import build_prompt

store = StudioStore(Path("demo.sqlite3"))          # use a throw-away database
source = store.create_manual_source({"kind": "web", "url": "https://example.invalid/a",
                                     "title": "Example", "text": "Some synthetic source text.",
                                     "author": "A. Writer"})
draft = store.create_draft(source["id"], "explainer", "Explain it", "General readers")
print(build_prompt(draft, store.source_for_draft(draft["id"]), "Evaluate the sources."))
```

Run it from the repository root. Run the tests with:

```sh
python -B -m unittest discover -p "test_*.py" -v
```

(9 tests; they use only temporary SQLite files under the repository folder and clean them up.)

## Privacy and limits

- Both prompt builders copy sources, evidence, draft and instruction **verbatim** into the text and print a warning first. They do not remove personal data. If you paste that text into an external service, any private data goes with it. The warning is not an approval mechanism or a leak filter.
- URL validation is a deliberate narrowing: URL paths and free text can still contain private information, and old records are not sanitised. Never point this at a real database; have a human review before sharing.
- The consistency check between a draft's snapshot and the project sources detects wrong-project and un-attached sources, but it is not authentication: use a draft and source produced by the store together.
- The tests include a Python-level guard against sockets, DNS and subprocesses. That is not OS-level network isolation and it does not cover native extensions or references taken earlier.
- Synthetic tests only. Not tested: a full product, real data, publishing, OS isolation. See `SECKI.en.md` for the detailed scope, provenance and limits.

## Turkey-specific notes

The prompts, warnings and error messages are in Turkish; the structure (modes such as `explainer`) and the code are language-neutral. The Turkish README is `README.tr.md`; the Turkish scope note is `SECKI.md`.

## Licence

MIT, see `LICENSE`. Copyright (c) 2026 Kemal (KarıncaAI). Python and SQLite runtimes keep their own licences. Report security issues privately, see `SECURITY.md`.
