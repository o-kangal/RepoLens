# Security Policy

## Reporting a vulnerability

Please report vulnerabilities **privately**, through GitHub: on the repository's **Security** tab, choose **Report a vulnerability** ([private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)). Do not open a public issue or pull request for a vulnerability before it is fixed.

Include what you found, how to reproduce it, the version or commit, and the impact you see. You will get an acknowledgement within 7 days and a first assessment within 14 days. Once a fix is released, the advisory is published with credit to you, unless you prefer otherwise.

## Scope

RepoLens indexes third-party repositories, so their content is untrusted. In scope:

- Executing code or hooks from an indexed repository.
- Reading files outside an indexed repository's root, for example through path traversal or symlinks.
- Ingesting from anything other than an allowed `https://github.com/` URL, or bypassing the size and file-count limits.
- Indexed content causing a write, or exposing secrets such as provider API keys, through prompt injection.
- Bypassing the public demo's rate limits, question length limit, CORS policy or read-only mode.

Getting the model to produce a wrong or misleading answer through prompt injection is a known limitation of LLMs. Report it when it crosses one of the boundaries above.

Out of scope: issues in dependencies without an effect on RepoLens (report those upstream), and missing hardening without a concrete attack.

## Supported versions

Only the latest release (and `main`) receives security fixes.

## For maintainers

Checks that run on every change: gitleaks (`gitleaks git --config .gitleaks.toml`), CodeQL, `pnpm audit`.
