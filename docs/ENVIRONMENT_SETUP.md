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

If PowerShell blocks script execution, allow it for the current session
only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This applies only to the current PowerShell session (the `Process` scope)
and does not persist once that window is closed, so it does not change the
execution policy for any other session or user on the machine.

To deactivate later:

```powershell
deactivate
```

## 2. Install dependencies

With the virtual environment activated, install the project in editable
mode along with the development dependency group (pytest, pytest-cov,
ruff, and Streamlit for the dashboard prototype's render tests). This is the
installation the complete test suite expects:

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
