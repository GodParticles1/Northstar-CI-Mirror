.PHONY: validate test snapshot

validate:
	PYTHONPATH=src python scripts/validate_stage1.py
	PYTHONPATH=src python -m unittest discover -s tests -v
	PYTHONPATH=src python -m personal_tech_os.snapshot --check snapshots/stage1-v1.json

test:
	PYTHONPATH=src python -m unittest discover -s tests -v

snapshot:
	PYTHONPATH=src python -m personal_tech_os.snapshot --output snapshots/stage1-v1.json

