# FastPost Social v3 — local shortcuts (Unix / Git Bash / WSL).
# Windows PowerShell: see scripts/dev.ps1

.PHONY: help install playwright playwright-deps verify-playwright dev start-prod

# Override if needed: make dev PYTHON=python3
PYTHON ?= python

help:
	@echo "FastPost — common targets"
	@echo "  make install          pip install -r requirements.txt"
	@echo "  make playwright       Chromium only (after install)"
	@echo "  make playwright-deps  Chromium + OS packages (Linux CI/Docker)"
	@echo "  make verify-playwright smoke-test headless Chromium (no Facebook)"
	@echo "  make dev              Flask dev server → http://localhost:5000"
	@echo "  make start-prod       Gunicorn via ./start.sh"

install:
	$(PYTHON) -m pip install -r requirements.txt

playwright: install
	cd backend && $(PYTHON) -m playwright install chromium

playwright-deps: install
	$(PYTHON) -m playwright install --with-deps chromium

verify-playwright: install
	cd backend && $(PYTHON) -m playwright install chromium
	$(PYTHON) scripts/verify_playwright_headless.py

dev: install
	cd backend && $(PYTHON) app.py

start-prod: install
	sh start.sh
