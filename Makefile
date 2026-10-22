# The project. `make setup` once, then `make app`. `make help` lists every command.

PY ?= .venv/bin/python
PORT ?= 8000
TASK ?=
BACKEND ?=

.PHONY: help setup test ping seed app serve check

help:            ## list the commands
	@grep -E '^[a-z]+:.*## ' Makefile | sed 's/:.*## /\t/' | expand -t 10

setup:           ## create .venv and install the dependencies
	@python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "Python 3.10 or later is needed"; exit 1; }
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt
	@echo "ready. Next: set your model in settings.yaml and its price line in pricing.yaml, set your key, then make ping."

test:            ## the starter's tests and yours; they spend no tokens
	$(PY) -m unittest discover -s tests -t .

ping:            ## two tiny model calls: your key, your model, its tool calling and its price line
	$(PY) -m harness.ping

seed:            ## reset the sandbox and seed your demo data, seed(None) (TASK=tasks/x.yaml: that task's inputs instead)
	$(PY) -m harness.seed $(if $(TASK),--task $(TASK))

app: seed        ## the one command: reset the sandbox, seed your demo data, then serve the app on http://localhost:8000
	$(if $(BACKEND),PROJECT_BACKEND=$(BACKEND)) $(PY) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

serve:           ## serve the app on http://localhost:8000 as the sandbox is, without resetting it
	$(if $(BACKEND),PROJECT_BACKEND=$(BACKEND)) $(PY) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

check:           ## the project's structure checks: one path, switches recorded, tasks valid, ledger
	$(PY) -m harness.check
