# Branching and handoff

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Stable releases only. Never push to it directly. |
| `develop` | Integration branch. Pull requests merge here. |
| `competition-mvp` | Controlled branch for the competition submission. Cut from `develop` and updated by pull request. |
| `feature/<area>-<summary>` | Work in progress, for example `feature/core-platform-day1` |

Both build chats work on separate feature branches and merge through pull requests, so neither overwrites the other.

## Commits

Use `type(scope): summary` in the imperative mood. Types: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `ci`. Describe the actual change, for example `feat(backend): add API key rotation with grace period`. Never commit `.env`, tokens, keys, passwords, model weights or logs. A CI workflow with secret scanning is planned and has not been added yet, so check staged files by hand.

## Handoff packages

A package is a ZIP of the files that changed, built as follows:

1. The ZIP root mirrors the repository root.
2. `.package/MANIFEST.txt` lists `<sha256>  <relative path>` for every file.
3. `scripts/install-package.ps1` verifies the manifest, compares each file with the repository, and refuses to overwrite a file that differs unless `-Force` is given. With `-Force` it first backs the old copy up to `.forge-backups`.

```powershell
Expand-Archive -Path "C:\Users\HP\Downloads\<package>.zip" -DestinationPath "C:\Users\HP\Downloads\<package>" -Force
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& "C:\Users\HP\Downloads\<package>\scripts\install-package.ps1" -Source "C:\Users\HP\Downloads\<package>" -Target "C:\Projects\forge" -DryRun
& "C:\Users\HP\Downloads\<package>\scripts\install-package.ps1" -Source "C:\Users\HP\Downloads\<package>" -Target "C:\Projects\forge"
```

Every handoff states, for each claim, whether it was executed ("Tested successfully") or not ("Blocked because ..."), together with the commands that verify it on the target machine.
