# Contributing to Tallyhawk

Thank you for your interest in contributing. This guide reflects the current FastAPI backend, Celery worker, and test workflow in this repository.

> Use this guide when making code, documentation, or configuration changes to the backend service.

## At a Glance

| Item | Details |
|---|---|
| Repository | `tallyhawk-api` |
| Primary stack | FastAPI, Celery, SQLModel, pytest |
| Local API | `uvicorn main:app --reload` |
| Local worker | `celery -A celery_app.celery_app worker --loglevel=info` |
| Test command | `pytest` |

---

## Table of Contents

- [Contributing to Tallyhawk](#contributing-to-tallyhawk)
  - [At a Glance](#at-a-glance)
  - [Table of Contents](#table-of-contents)
  - [Code of Conduct](#code-of-conduct)
  - [Getting Started](#getting-started)
  - [How to Contribute](#how-to-contribute)
  - [Branch Naming](#branch-naming)
  - [Commit Messages](#commit-messages)
  - [Pull Request Process](#pull-request-process)
    - [PR Description Template](#pr-description-template)
  - [What does this PR do?](#what-does-this-pr-do)
  - [Why is this change needed?](#why-is-this-change-needed)
  - [How was it tested?](#how-was-it-tested)
  - [Related issue](#related-issue)
  - [Screenshots (if applicable)](#screenshots-if-applicable)
  - [Code Style](#code-style)
  - [Reporting Bugs](#reporting-bugs)
  - [Questions and Suggesting Features](#questions-and-suggesting-features)
  - [Local Commands](#local-commands)

---

## Code of Conduct

This project follows a simple rule: be respectful. Constructive criticism is welcome; personal attacks are not. Any contributor who violates this will be removed from the project.

---

## Getting Started

1. Fork the repository.
2. Clone your fork locally:
   ```bash
   git clone https://github.com/your-username/tallyhawk-api.git
   cd tallyhawk-api
   ```
3. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Copy the environment template and fill in the values:
   ```bash
   cp .env.example .env
   ```

Before you run the app, make sure you have these services available:

| Service | Purpose |
|---|---|
| Postgres | Stores users, documents, and extraction results |
| Redis | Celery broker for background jobs |
| Supabase Storage | Stores uploaded documents |
| Gemini API key | Powers document extraction |
| Stripe keys | Required only if you are working on billing flows |

---

## How to Contribute

1. Check the [open issues](https://github.com/ymahrous/edocai/issues) before starting work to avoid duplicating effort.
2. If no issue exists for what you want to change, open one first and wait for confirmation before starting.
3. Fork the repo and create your branch from `main`.
4. Make your changes following the code style guidelines below.
5. Run the relevant checks locally, usually `pytest` and the app startup commands relevant to your change.
6. Open a pull request against `main`.

Recommended flow:

- Keep the change focused on one problem.
- Prefer small, reviewable commits.
- Include test coverage whenever the behavior changes.

---

## Branch Naming

Use the following prefixes depending on the type of change:

| Type | Prefix | Example |
|---|---|---|
| New feature | `feat/` | `feat/document-upload-validation` |
| Bug fix | `fix/` | `fix/celery-task-retry` |
| Refactor | `refactor/` | `refactor/auth-dependency` |
| Documentation | `docs/` | `docs/update-env-setup` |
| Performance | `perf/` | `perf/storage-upload-path` |
| Chore / config | `chore/` | `chore/update-requirements` |

---

## Commit Messages

Follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <short description>

[optional body]

[optional footer]
```

**Examples:**
```
feat(auth): add password reset endpoint
fix(tasks): retry failed document extraction jobs
refactor(api): simplify upload validation flow
chore: update dependencies for Python 3.11
```

**Rules:**
- Use the imperative mood in the description ("add" not "added")
- Keep the first line under 72 characters
- Reference related issues in the footer: `Closes #12`

Suggested pattern:

```text
<type>(<scope>): <short description>
```

---

## Pull Request Process

1. **One concern per PR** — keep PRs focused. A PR that fixes a bug and adds a feature will be asked to split.
2. **Fill out the PR template** — describe what changed and why, not just what.
3. **Link the related issue** — every PR should close or reference an issue.
4. **Run the relevant checks** — at minimum, `pytest` should pass for code changes.
5. **Request a review** — assign a maintainer when the PR is ready.
6. **No force-pushing** after review has started unless a maintainer asks for it.

Helpful review checklist:

- The change solves one clear problem.
- The diff is easy to follow.
- Tests were added or updated where behavior changed.
- Any environment or deployment notes are included in the PR description.

### PR Description Template

When opening a PR, use this structure:

## What does this PR do?
A short summary of the change.

## Why is this change needed?
The problem it solves or the improvement it makes.

## How was it tested?
Steps you took to verify the change works locally.

## Related issue
Closes #<issue number>

## Screenshots (if applicable)

---

## Code Style

This project is a Python backend. Follow these rules:

**Python**
- Use type hints for new functions and public APIs.
- Prefer clear, explicit code over clever abstractions.
- Keep changes small and focused on the owning module.

**FastAPI and SQLModel**
- Keep request validation close to the route or schema that owns it.
- Reuse shared dependencies from `dependencies.py` and shared DB helpers from `database.py`.
- Prefer typed models and schemas over ad hoc dictionaries when a response shape is stable.

**Testing**
- Add or update `pytest` coverage for behavior changes.
- Keep fixtures in `conftest.py` reusable and narrow.
- When touching async or background-processing code, verify the worker path as well as the API path.

**Naming**
- Modules and helper files: `snake_case`
- Classes: `PascalCase`
- Functions, variables, and route handlers: `snake_case`

When in doubt, follow the nearest existing pattern in the repository rather than introducing a new style.

---

## Reporting Bugs

Open a [GitHub Issue](https://github.com/ymahrous/edocai/issues/new) with the following information:

- **Description** — what happened vs. what you expected
- **Steps to reproduce** — numbered, minimal steps
- **Environment** — OS, Python version, relevant service versions
- **Screenshots or error logs** if applicable

Please search existing issues before opening a new one.

---

## Questions and Suggesting Features

Open a [GitHub Issue](https://github.com/ymahrous/edocai/issues/new) with:

- **Problem** — what gap or pain point this addresses
- **Proposed solution** — what you'd like to see
- **Alternatives considered** — other approaches you thought of
- **Additional context** — mockups, examples, or prior art

Feature requests are not guaranteed to be implemented, but all suggestions are read and considered.

## Local Commands

These are the main commands contributors use in this repository:

```bash
make install
make run
make worker
make test
```

If you prefer running the tools directly:

```bash
uvicorn main:app --reload
celery -A celery_app.celery_app worker --loglevel=info
pytest
```

| Command | Purpose |
|---|---|
| `make install` | Install Python dependencies from `requirements.txt` |
| `make run` | Start the FastAPI development server |
| `make worker` | Start the Celery worker |
| `make test` | Run the test suite |
