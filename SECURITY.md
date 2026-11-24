# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| latest commit on `main` | yes |
| older | no |

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it privately with GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability** (https://github.com/KemalG-u/karincaai-icerik-studyosu/security/advisories/new).

Include: affected version, a description, steps to reproduce, and the impact you see. Never include real secrets or personal data in a report; describe where they appear instead.

## What to expect

This is a one-person project maintained on a best-effort basis. The maintainer aims to send an initial response within 14 days of a report. A fix date is not promised; the maintainer will tell you what is planned.

## Scope

In scope: the Python code in `studio/` (for example path handling, SQL handling, URL validation bypasses and prompt-scope checks). Out of scope: weaknesses of the Python or SQLite runtimes, and private data a user chooses to paste into an external service from a generated prompt (the README documents this limit).
