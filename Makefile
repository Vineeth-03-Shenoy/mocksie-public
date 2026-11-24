# `make security` runs the same checks as the GitHub workflow (scripts/security.py).
# No `make` on Windows? Run the script directly:  python scripts/security.py
.PHONY: security security-fast hooks

security:            ## every check: lockfiles, osv, pip-audit, npm audit, trivy fs, secrets, trivy images
	python scripts/security.py

security-fast:       ## everything except the slow image build/scan
	python scripts/security.py locks osv pip-audit npm-audit trivy-fs secrets

hooks:               ## enable the pre-commit secret scan for this clone
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit
