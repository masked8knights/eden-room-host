# Eden Room Host

A tiny, self-contained launcher for the [Eden](https://eden-emu.dev/) dedicated
room server, with the [playit.gg](https://playit.gg) agent built in so friends
can join over the internet without port forwarding.

One download, no Python, no terminal.

![Eden Room Host](assets/icon.png)

## Downloads

Grab the file for your system from the [Releases](../../releases) page:

| File | System |
|------|--------|
| `Eden-Room-Host-x86_64.AppImage` | Linux (x86_64). chmod +x and run. |
| `EdenRoomHost.exe` | Windows (x86_64). Just run it. |

Both bundle the `eden-room` dedicated server and the `playit` agent. The Eden
emulator itself is **not** included; install it separately and add your game
folders there. This app reads the same folders and lets you pick a game when
opening the room.

## First run

1. **Eden**: install [Eden](https://eden-emu.dev/), add your game folders in
   its settings, and make sure your prod.keys / firmware are set up.
2. **playit**: start Eden Room Host. If the playit agent has never been
   claimed, the setup window starts it and opens the claim link in your
   browser. Sign in to (or create) your playit.gg account and click Agree.
3. Pick a game, set a room name and password, and press **Start Room**.
   The app starts the room server and the playit tunnel, and the LOG panel
   at the bottom shows everything live.

Share your playit.gg address (shown on your
[playit tunnels page](https://playit.gg/account/tunnels)) plus the room
password with your friends. In Eden they connect to that address with the
Direct Connect / multiplayer room dialog.

## Notes

- The room server listens on UDP **24872** on localhost; playit forwards it.
- On Linux the app prefers the `playit` systemd service if you have one
  installed (it can start and stop it for you). Otherwise it runs the bundled
  agent itself.
- Stop Room kills the server immediately; the LOG and the status line always
  tell the truth.
- The Windows `eden-room.exe` in some releases is built from Eden's own
  source (`src/yuzu_room_standalone`) by this repo's CI.

## Building from source

Needs Python 3.9+ and PyInstaller. On Linux:

```sh
python3 -m PyInstaller --onefile --name EdenRoomHost \
  --add-binary "bin/eden-room:." --add-binary "bin/playit:." \
  --add-binary "assets/icon.png:." app/eden_room_host.py
```

Tag a commit (`git tag v1.0 && git push --tags`) and GitHub Actions builds
both artifacts automatically.

## License

The launcher code is released under the MIT license. Eden and eden-room are
GPL-3.0 (Eden Emulator Project); the playit agent has its own license at
[playit-cloud/playit-agent](https://github.com/playit-cloud/playit-agent).
