# Magic Mapper for webOS

A TV-native Homebrew interface for
[Magic Mapper](https://github.com/andrewfraley/magic_mapper). Discover, disable,
or redirect buttons on an LG Magic Remote without editing JSON over SSH.

This is an independent wrapper, not an official Magic Mapper project. It keeps
the upstream mapper unmodified and pinned, so the interface adds no maintenance
or release burden to the upstream repository.

## What it does

- Discovers a remote button by pressing it and suppresses that discovery press.
- Disables branded shortcuts such as Netflix, Prime Video, Disney+, Rakuten TV,
  or Alexa.
- Opens an app selected from the apps installed on the TV.
- Makes one remote button behave like another.
- Covers every action supported by the pinned Magic Mapper runtime: OLED light,
  energy saving, eye comfort, Dynamic Tone Mapping, screen off, IR, webhooks,
  TCP commands, HDMI-CEC, and PicCap.
- Groups actions into remote-friendly TV screens with strict input validation
  and one-level-at-a-time Back navigation.
- Can disable the Magic Remote pointer globally from Settings, with a warning
  and a reversible restart flow.
- Shows whether the mapper actually holds the remote input device.
- Restores individual buttons and supports clean removal from the TV UI.
- Imports supported mappings from a manual Magic Mapper installation on first
  setup.

## Requirements

- A rooted LG webOS TV.
- Homebrew Channel running as root.
- Python 3 on the TV. This is present on the currently tested webOS versions.

The first hardware target is an LG C3 running webOS 25 (internal webOS 10.3.1).
Wider hardware compatibility has not yet been claimed.

## How the wrapper works

The source pin is recorded in [`vendor/upstream.json`](vendor/upstream.json).
[`vendor/magic_mapper.py`](vendor/magic_mapper.py) is an unmodified copy from
that commit. Packaging verifies its SHA-256 checksum and fails if it has drifted.

The wrapper in [`runtime/managed_mapper.py`](runtime/managed_mapper.py) owns the
input loop and imports upstream actions and button definitions. This is what
adds one-shot discovery, authoritative status, graceful input release, and app
lifecycle handling without patching upstream code.

To re-fetch the current pin:

```sh
npm run sync-upstream
```

Updating the pin is a deliberate review step: change the commit and checksum in
`vendor/upstream.json`, sync, inspect the diff, and run the full hardware checks.

## Build

Node.js 22 or newer is recommended.

```sh
npm ci
npm run check
npm run package
```

The IPK is written to `dist/`. `npm run manifest` creates the release manifest
consumed by Homebrew Channel.

## State and removal

Mappings and runtime state live under `/var/lib/webosbrew/magic-mapper`. The
startup hook lives at `/var/lib/webosbrew/init.d/50-magic-mapper`.

Uninstalling from the app stops the process, releases the exclusive input grab,
removes the hook and state directory, and then asks webOS to remove the app.

## License and attribution

Released under the MIT License. The vendored upstream file retains Andy Fraley's
copyright and license; wrapper code is copyright Afonso Ramos.
