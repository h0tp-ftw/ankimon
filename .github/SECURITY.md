# Security Policy

## Supported Versions

We provide security fixes for the latest version of Ankimon (Experimental and Stable) on AnkiWeb and the `main` branch.

## Reporting a Vulnerability

If you discover a security vulnerability in Ankimon, please report it privately rather than opening a public issue, discussion, or pull request.

You can submit a report through [GitHub's private vulnerability reporting](https://github.com/h0tp-ftw/ankimon/security/advisories/new).

Security reports are handled by:
- [@h0tp-ftw](https://github.com/h0tp-ftw)
- [@scotej](https://github.com/scotej)

If you're unable to use GitHub's reporting form, you can reach out to either maintainer on GitHub to arrange another way to report the issue privately. Please avoid sharing any vulnerability details publicly.

To help us investigate, please include:
- Your Ankimon and Anki versions, along with your operating system
- Steps to reproduce the issue, or a proof of concept
- The potential impact, such as data loss, code execution, or exposure of sensitive information

We aim to acknowledge reports within **7 days** and provide a fix or a plan for addressing the issue within **30 days**.

We're grateful to anyone who helps keep Ankimon secure. Reporters will be credited in the published security advisory unless they prefer to remain anonymous.

## Scope

This policy covers the code in `src/Ankimon` that ships with the add-on, including the self-updater, mobile/media sync, and web views.

Development tooling in `harness/` and `tests/`, Anki itself, and the upstream `poke-engine` are outside the scope of this policy. If you find an issue in one of those components, please report it to the relevant project instead.
