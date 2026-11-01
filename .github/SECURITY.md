# Security Policy

## Supported Versions

Only the latest release on AnkiWeb and the `main` branch receive security fixes.

## Reporting a Vulnerability

Please **do not** open a public issue, discussion, or pull request for security problems.

Report privately through GitHub's private vulnerability reporting:
https://github.com/h0tp-ftw/ankimon/security/advisories/new

Reports are handled by the maintainers:

- [@h0tp-ftw](https://github.com/h0tp-ftw)
- [@scotej](https://github.com/scotej)

If you cannot use the advisory form, contact either maintainer directly on GitHub
and ask for a private channel. Do not include vulnerability details in public messages.

Please include:

- Ankimon and Anki versions, and your operating system
- Steps to reproduce, or a proof of concept
- The impact you expect (for example data loss, code execution, or leaked data)

We aim to acknowledge reports within 7 days and to share a fix or plan within 30 days.
Reporters are credited in the published advisory unless they ask otherwise.

## Scope

In scope: code in `src/Ankimon` (the shipped add-on), including the self-updater,
mobile/media sync, and the web views.

Out of scope: the `harness/` and `tests/` dev tooling, Anki itself, and the upstream
poke-engine. Please report issues in those to their own projects.
