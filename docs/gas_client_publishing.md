# GAS Client Publishing Notes

These notes are for project maintainers who publish the standalone
`gas-client` package to PyPI. They are kept outside the package README so the
PyPI page remains focused on SDK users.

## Publishing Checklist

Before publishing a new release:

- Confirm the package name and project metadata in `packages/gas-client/pyproject.toml`.
- Update the package `version`.
- Sync the package copy from the repository root if `gas_client/` changed.
- Run the client tests from the repository root.
- Build and inspect the package artifacts.

```powershell
cd <repo-root>
.\packages\gas-client\sync_from_repo.ps1
.\.venv\Scripts\python.exe -m pytest tests\test_gas_client.py

cd packages/gas-client
python -m build
python -m twine check dist/*
```

Upload only when ready:

```powershell
python -m twine upload dist/*
```

Do not commit built `dist/`, `build/`, or `*.egg-info/` artifacts.
