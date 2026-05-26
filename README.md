# Meetings Minutes

> Capture and summarize meeting minutes

## Install

Download the latest release for your platform from the [Releases](https://github.com/you/meetings-minutes/releases) page.

| Platform | Artifact |
| --- | --- |
| Windows | `meetings-minutes.exe` |
| macOS (Apple Silicon) | `meetings-minutes-arm64.dmg` |
| macOS (Intel) | `meetings-minutes-x86_64.dmg` |
| Linux (x86_64) | `meetings-minutes-x86_64.AppImage` |

> macOS: the app is ad-hoc signed, not notarized. On first launch, right-click → Open, or run `xattr -cr "/Applications/Meetings Minutes.app"` to clear the quarantine flag.

## Development

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/you/meetings-minutes
cd meetings-minutes
uv sync                  # add --extra linux on Linux for PySide6
uv run python main.py
```

### Building a local binary

```bash
uv pip install pyinstaller
uv run pyinstaller meetings-minutes.spec --clean --noconfirm
```

### Nix

```bash
nix run            # run the app
nix build          # build the package into ./result
nix develop        # enter the devshell with uv preconfigured
```

## Release

Tag the commit and push — GitHub Actions builds Windows/macOS/Linux artifacts and attaches them to a new GitHub release:

```bash
git tag v0.1.0
git push --tags
```
