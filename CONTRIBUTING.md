# Contributing

1. Fork and branch from `main`.
2. `pip install -r requirements-dev.txt && python -m pytest -q tests` must pass. Tests mock inference; no GPU is needed.
3. Frontend changes: `cd web && npm install && npm run build`, and commit the updated `web/dist`.
4. Never commit secrets, databases, releases or real corpora.
5. Describe the change and how you tested it in the PR.
