# Starlight helpers

- `service.py`: Kodi music queue monitor.
- `default.py`: argument router for skin actions.
- `resources/lib/`: queue, music actions, search, and shared Kodi boundaries.
- `tests/`: standard-library tests with small Kodi fakes.
- Write original code from approved Starlight contracts and official Kodi APIs.
- Keep provider credentials inside the provider add-ons.
- Keep queue ownership with Kodi and the music provider.
- Keep hub settings in native skin strings and booleans.
- License: GPL-2.0-or-later (`LICENSE.txt`).

## Verify

- Run `python3 -m unittest discover -s tests`.
- Run `python3 -m compileall -q default.py service.py resources/lib tests`.
- Run `git diff --check`.
- Verify real playback only with explicit device authorization.

## Publish

- Follow `https://github.com/primez-x/kodi.addons/blob/main/VERSIONING.md`.
- Raise one version component per release. Reset later components to zero.
- Add release news with the new version on the first line.
- Preserve `.githooks/publish_check.py` as a verbatim copy of the shared validator.
- Enable the hook with `git config core.hooksPath .githooks`.
- Run configured tests before a publish. The hook checks committed exports.
