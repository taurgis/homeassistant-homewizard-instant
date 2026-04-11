# Contributing

Thanks for contributing. Keep changes focused and consistent with the existing style.

## Development setup

```bash
# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python test dependencies
python3 -m pip install -r requirements_test.txt

# Install release tooling
npm install
```

## Verification

Run these before opening a PR:

```bash
# Linting
python3 -m ruff check custom_components/homewizard_instant/

# Type checking
python3 -m mypy --strict custom_components/homewizard_instant/

# Tests with coverage
pytest tests/ -q --cov=custom_components/homewizard_instant --cov-fail-under=95
```

## Releases

Changesets handles release preparation for this repository.

```bash
# Add a changeset on feature/fix branches when the change should be released
npm run changeset
```

After changesets land on `main`, GitHub Actions opens or updates a `Release` PR with the version bump, changelog changes, and synced Home Assistant manifest version. Merging that PR pushes the matching `v*` tag, and the existing release workflow turns that tag into a GitHub release.

For release candidates, enter prerelease mode before preparing the next RC batch and exit it before the final stable cut:

```bash
npm run changeset:pre:enter
npm run changeset:pre:exit
```