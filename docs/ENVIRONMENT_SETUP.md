# Environment Setup

This guide covers creating a local Python environment for AI Market
Intelligence on Windows using PowerShell. It does not cover connecting any
live API — see [PROJECT_STATE.md](../PROJECT_STATE.md) for current
integration status.

## 1. Create and activate a virtual environment

From the repository root, in PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution, you may need to allow local scripts
for your user (run once, in an elevated or normal PowerShell session as
appropriate for your environment):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

To deactivate later:

```powershell
deactivate
```

## 2. Install dependencies

With the virtual environment activated, install the project in editable
mode along with the development dependency group (pytest, pytest-cov,
ruff):

```powershell
python -m pip install -e ".[dev]"
```

## 3. Create a local `.env` file

Copy the example file and fill in your own values locally. Never commit
this file — it is excluded by `.gitignore`.

```powershell
Copy-Item .env.example .env
```

Then edit `.env` in a text editor and fill in only the credentials you
actually have and intend to use. Leave any provider you are not using
blank.

**Never commit `.env`.** It is already listed in `.gitignore`, but always
double-check `git status` before staging changes to confirm it is not
accidentally included.

## 4. Run tests

```powershell
python -m pytest
```

## 5. Run linting

```powershell
python -m ruff check .
```
