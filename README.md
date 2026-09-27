# Smash Replay Recorder

Saves every Super Smash Bros. Ultimate replay on your Switch as its own video file, automatically.

It plays each replay on your Switch, records it through OBS, and moves on to the next one. You can leave it running and split a big collection over as many sessions as you like; it always picks up where it left off.

> **Early test version.** It works on the setup it was built on (Elgato HD60 X capture card). Other setups should work but haven't been tested yet. If something goes wrong, [open an issue](../../issues) with a screenshot.

## What you need

- A **Switch in its dock**, with the dock's HDMI going into a **capture card**, and the capture card plugged into your PC
- A **Windows 10 or 11 PC** with **Bluetooth** (built in, or any USB Bluetooth adapter)
- **[OBS Studio](https://obsproject.com/)** (free), open and showing your Switch
  - In OBS: **Tools → WebSocket Server Settings → tick "Enable WebSocket server" → OK**
- Disk space: depends on your OBS quality settings. At high quality, plan on roughly 20 GB per hour of matches.

## Install

1. Download **SmashReplayRecorder-Setup.exe** from the [Releases page](../../releases).
2. Run it. If Windows says *"Windows protected your PC"*, click **More info → Run anyway** (the app isn't code-signed yet).
3. Click **Yes** when Windows asks for permission. That's the only time it asks.
4. If setup says Windows needs to restart, restart, then open **Smash Replay Recorder** from the Start menu.

## Record your replays

1. Open OBS, then **Smash Replay Recorder**.
2. Pick a folder for the videos and press **Get started**.
3. **First time only:** on your Switch, go to **HOME → Controllers → Change Grip/Order**. The recorder connects by itself.
4. On your Switch, open **Smash → Vault → Replays → Replay Data** and press **A** on the first replay.
5. The recorder counts your replays and shows how long they'll take. Pick a session length and press **Start recording**.
6. Leave the Switch and PC alone while it records. To keep going another day, open the recorder and press **Continue**.

## Good to know

- **Your replays stay on your Switch.** Nothing on the console is changed or deleted.
- **Bluetooth:** while the recorder is open, your PC's Bluetooth is used to talk to the Switch, so Bluetooth headphones or mice will disconnect. Windows gets it back when you close the recorder. A cheap USB Bluetooth adapter just for the recorder avoids this.
- **Older replays:** replays from older game versions sometimes stop partway. The playable part is saved as `replay-NNN-incomplete.mp4`, and the recorder moves on.
- The first couple of seconds after "GO!" are skipped while the replay controls are hidden.
- **Audio out of sync in OBS?** Add an **Audio Input Capture** source for your capture card's audio device and mute the capture card source's own audio. It keeps sync much steadier than the default.
- **Uninstall** from Windows Settings → Apps. This also removes the recorder's Bluetooth service and helper.

## How it works

The recorder pretends to be a wireless Pro Controller (using [NXBT](https://github.com/Brikwerk/nxbt) inside a small Linux helper that runs in WSL) and presses buttons on the Switch. It watches the capture card through OBS to know which screen the Switch is on, starts and stops OBS recordings around each match, and saves each one as an MP4.

Building it yourself: see [BUILDING.md](BUILDING.md).

## Code signing policy

Every installer is built by [GitHub Actions](.github/workflows/build.yml) from the source code in this repository, on GitHub's own build machines.

Signing: free code signing from [SignPath Foundation](https://signpath.org) has been applied for. Until it's approved, releases are unsigned (hence the Windows warning above).

- Committers and reviewers: [grant-m-nelson](https://github.com/grant-m-nelson)
- Approvers: [grant-m-nelson](https://github.com/grant-m-nelson)

Privacy: the recorder doesn't send any information over the internet. It only talks to OBS and the controller helper on your own PC. (If WSL isn't installed yet, setup downloads it from Microsoft.)

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the software bundled in the installer.

Not affiliated with or endorsed by Nintendo. Super Smash Bros. and Nintendo Switch are trademarks of Nintendo.
