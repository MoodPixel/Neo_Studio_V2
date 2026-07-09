# Security Policy

Thank you for helping keep Neo Studio safe.

Neo Studio is a local-first creative AI workspace. Because it can interact with local files, local AI backends, API keys, model paths, extensions, generated media, and runtime data, security reports are taken seriously.

Please do **not** open a public GitHub issue for vulnerabilities that could expose user data, credentials, local paths, or private system details.

---

## Supported Versions

Neo Studio is currently in active V2 development. Formal stable release branches have not been established yet.

Security support is currently handled on a best-effort basis for the latest public version of the project.

| Version / Branch | Supported |
| --- | --- |
| Latest public `main` branch | :white_check_mark: |
| Latest tagged release, when available | :white_check_mark: |
| Older commits or archived branches | :x: |
| Unofficial forks or modified builds | :x: |

If you are using an older copy of Neo Studio, please update to the latest public version before reporting a vulnerability, unless the issue also affects the latest version.

---

## Reporting a Vulnerability

If you believe you have found a security vulnerability, please report it privately.

Use one of the following methods:

1. Use GitHub's private vulnerability reporting feature, if it is enabled for this repository.
2. Contact the maintainer using the contact method listed in the repository README.

Please include as much detail as possible so the issue can be reviewed properly.

Useful information includes:

- A clear description of the vulnerability
- Steps to reproduce the issue
- The affected workspace or feature
- Your operating system
- Python version
- Neo Studio version, commit, or download date
- Backend involved, if relevant
- Whether API keys, local paths, generated files, or user data could be exposed
- Screenshots, logs, or proof-of-concept details when safe to share

Please avoid sharing active API keys, private tokens, personal files, or sensitive user data in the report.

---

## What Counts as a Security Issue

Examples of security issues include:

- API keys or tokens being exposed in logs, UI, exports, or committed files
- Local user data being written into the main repository by mistake
- Runtime data leaking private paths, prompts, outputs, or backend details
- Unsafe file handling, path traversal, or unintended file overwrite behavior
- Dependency vulnerabilities that affect Neo Studio directly
- Unsafe handling of uploaded files, generated files, or extension files
- Backend connection behavior that could expose private credentials or local services
- Extension behavior that can access or modify files outside its intended scope

---

## What Is Usually Not a Security Issue

The following are usually better reported as normal bugs or feature requests:

- UI layout problems
- Missing documentation
- Backend setup confusion
- Model output quality issues
- Prompt behavior issues
- Local installation problems
- Broken workflows that do not expose data or credentials
- Issues caused only by heavily modified third-party forks

If you are unsure, report privately first.

---

## Response Expectations

Neo Studio is maintained by a small development team / independent maintainer, so response times may vary.

Expected handling:

1. The report will be reviewed privately.
2. The issue will be reproduced when possible.
3. If accepted, a fix or mitigation plan will be prepared.
4. Public disclosure will be delayed until users have a reasonable path to update or avoid the issue.
5. If declined, the reason will be explained when possible.

For serious vulnerabilities, please allow reasonable time for investigation before public disclosure.

---

## Disclosure Guidelines

Please do not publicly disclose a vulnerability until it has been reviewed and patched or mitigated.

When a fix is available, the project may publish a security note or changelog entry that summarizes the issue without exposing unnecessary exploit details.

Credit may be given to the reporter if they want to be acknowledged.

---

## Handling Secrets

Never commit or share:

- API keys
- Access tokens
- Private backend URLs
- Credentials
- Personal user data
- Private model paths
- Private runtime data
- Local machine-specific configuration

If secrets are accidentally committed, rotate or revoke them immediately.

---

## Local-First Security Notes

Neo Studio is designed around local-first workflows, but local-first does not mean risk-free.

Users should still be careful with:

- Third-party extensions
- Custom nodes
- Unknown model files
- External APIs
- Shared workflow files
- Publicly uploaded logs
- Screenshots that reveal local paths or API keys

Only install extensions, models, and backend tools from sources you trust.

---

## Thank You

Responsible security reports help protect Neo Studio users and improve the project.

Thank you for reporting issues carefully and privately.
