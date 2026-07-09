# Contributing to Neo Studio

Thank you for your interest in contributing to Neo Studio.

Neo Studio is a local-first creative AI workspace focused on structured workflows for image generation, video tools, prompting, captioning, assistant systems, roleplay systems, and future multimodal creative tools.

This project is under active development. Contributions are welcome, but they need to follow the project structure and design direction carefully.

---

## Project Status

Neo Studio is currently in active V2 development.

That means some areas are stable, while others are still being designed, tested, or reorganized. Before submitting major changes, please open an issue first so the idea can be discussed.

Please do not submit large rewrites, architectural changes, or unrelated feature bundles without prior discussion.

---

## Ways You Can Contribute

You can help by:

- Reporting bugs
- Improving documentation
- Suggesting workflow improvements
- Testing setup instructions
- Reporting missing dependency or backend setup issues
- Suggesting UI/UX improvements
- Improving error messages
- Helping with extension compatibility notes
- Creating clear reproduction steps for broken behavior

Code contributions may be accepted later, but for now, documentation, testing feedback, setup reports, and focused fixes are the most useful.

---

## Before You Contribute

Please check the following first:

1. Search existing issues to see if the problem has already been reported.
2. Read the README carefully.
3. Make sure your request is related to Neo Studio’s actual project direction.
4. Keep each issue or pull request focused on one topic.
5. Avoid combining unrelated changes.

Good contributions are small, clear, and easy to review.

---

## Reporting Bugs

When reporting a bug, please include:

- Your operating system
- Python version
- How you launched Neo Studio
- What workspace or tab you were using
- Which backend you were using, if relevant
- The exact error message or traceback
- Steps to reproduce the issue
- Screenshots, if helpful
- Whether the issue happens every time or only sometimes

A good bug report explains what happened, what you expected to happen, and how someone else can reproduce it.

### Example

```txt
OS: Windows 11
Python: 3.10
Workspace: Image
Backend: ComfyUI
Issue: Backend test succeeds, but generation fails.
Steps:
1. Open Neo Studio
2. Go to Image workspace
3. Select ComfyUI backend profile
4. Click Generate
5. Error appears: ...
```

---

## Suggesting Features

Feature suggestions are welcome, but they should explain the workflow need.

Please include:

- What problem the feature solves
- Who would use it
- Where it would fit in the UI
- Whether it affects existing workflows
- Any references, screenshots, or examples

Please avoid vague requests like:

```txt
Add more AI features.
```

A better request would be:

```txt
Add a way to save reusable image generation presets per model family, so users can quickly switch between SDXL, Flux, and Wan workflows without rebuilding settings each time.
```

---

## Pull Request Guidelines

Before opening a pull request:

1. Open an issue first for anything larger than a small documentation fix.
2. Keep your PR focused.
3. Explain what changed and why.
4. Include screenshots for UI changes.
5. Do not include unrelated formatting changes.
6. Do not commit generated files, cache files, local runtime data, or personal configuration.
7. Make sure the app still launches after your change.

Pull requests should use clear titles.

Good examples:

```txt
Fix README backend setup wording
Add troubleshooting notes for ComfyUI connection errors
Improve Image workspace dependency documentation
```

Bad examples:

```txt
Update stuff
Fix things
Big changes
Refactor everything
```

---

## Local Runtime Data

Neo Studio is designed to keep user-specific runtime data outside the main repository whenever possible.

Please do not commit:

- Local backend status files
- User profiles
- Runtime logs
- Generated images or videos
- Local model paths
- API keys
- Personal settings
- Cache files
- Temporary test output
- Machine-specific configuration

If you are unsure whether a file should be committed, open an issue and ask first.

---

## Private Developer Records

Some project planning, testing notes, and system records are maintained outside the public GitHub repository.

Please do not submit or request private development records unless they have been intentionally prepared for public documentation.

Public documentation should explain how to use Neo Studio.

Internal records are for development planning, audits, and implementation tracking.

---

## Documentation Contributions

Documentation improvements are very welcome.

Useful documentation contributions include:

- Clearer setup steps
- Better backend setup instructions
- Troubleshooting notes
- Missing dependency notes
- Explanation of workspace behavior
- Clarifying what a setting does
- Correcting outdated wording
- Improving screenshots or examples

Documentation should be written for real users, not only developers.

Use clear language. Avoid internal phase names unless they are part of the public UI.

---

## Backend and Extension Notes

Neo Studio may support multiple backend families and creative workflows.

When documenting backend behavior, please be specific about:

- Which backend is affected
- Whether the issue is local or API-based
- Whether a profile already exists
- Whether the user needs to add a path, URL, or API key
- Whether custom nodes or extensions are required
- What error appears when setup is incomplete

Do not assume all users are using the same backend.

---

## Code Style

Code should prioritize:

- Clarity
- Stability
- Maintainability
- Small focused changes
- Safe error handling
- Clear user-facing messages
- Respect for existing workflow structure

Avoid quick patches that hide the real problem.

If a fix changes behavior, explain the impact clearly in the pull request.

---

## UI and UX Contributions

Neo Studio is workflow-driven software.

UI contributions should consider:

- What the user is trying to do
- What state the system is in
- What feedback the user needs
- How errors are explained
- Whether the layout remains understandable
- Whether the feature works across different backend types

Do not add UI controls without explaining what they do and how they affect the workflow.

---

## Security

Never commit secrets.

This includes:

- API keys
- Tokens
- Private URLs
- Local credentials
- Personal user data
- Private backend configuration

If you discover a security issue, please report it privately to the maintainer instead of opening a public issue.

Maintainer contact can be found in the repository README.

---

## Code of Conduct

All contributors are expected to follow the project’s Code of Conduct.

Please keep discussions respectful, constructive, and focused on improving the project.

---

## License

By contributing to Neo Studio, you agree that your contributions will be licensed under the same license as the project.

Please check the repository LICENSE file for details.

---

## Final Notes

Neo Studio is being built as a structured creative system, not just a collection of disconnected tools.

The best contributions are thoughtful, focused, and aligned with the project’s long-term direction.

Thanks for helping improve Neo Studio.
