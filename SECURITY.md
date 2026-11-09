# Security policy

Desktop Cleaner moves and deletes files on the user's own machine, so correctness is a safety matter.

## Supported versions

| Version | Supported |
|---|---|
| 2.1.x | yes |
| 2.0.x | security fixes only |
| 1.x | no |

## Reporting a vulnerability

If you find a way to make Desktop Cleaner delete or move something it should not (path traversal in rules, a detector that grades user data as *safe*, a journal entry that restores to the wrong place), please do not open a public issue. Use GitHub's private vulnerability reporting on this repository, or email the maintainer listed in `pyproject.toml`.

You will get an acknowledgement within a few days and a fix or mitigation as soon as possible. Credit is given in the changelog unless you prefer otherwise.

## What Desktop Cleaner never does

- It never runs with elevated privileges and never asks for them.
- It never touches system folders (`Windows`, `Program Files`, `/usr`, `/System`); protected areas are reported, not cleaned.
- It never sends anything over the network. There is no telemetry.
- It never deletes user data permanently without the user explicitly choosing to skip the recycle bin.
