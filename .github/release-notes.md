Standalone builds of Combat. No Python or pygame needed.

| Platform | Download |
|---|---|
| macOS, Apple silicon | `Combat-macos-arm64` or `Combat-macos-arm64-app.zip` |
| macOS, Intel | `Combat-macos-x86_64` or `Combat-macos-x86_64-app.zip` |
| Linux, x86_64 | `Combat-linux-x86_64` |
| Windows, x86_64 | `Combat-windows-x86_64.exe` |

**macOS.** The builds are ad-hoc signed, not notarised, so macOS quarantines them
after download and refuses to open them:

```
xattr -dr com.apple.quarantine ~/Downloads/Combat-macos-arm64
chmod +x ~/Downloads/Combat-macos-arm64
```

**Linux.** Release assets do not carry the executable bit:
`chmod +x Combat-linux-x86_64`.

**Controls.** P1 `WASD` + `Space`, P2 arrows + `Return`. `P` pause, `M` mute,
`Esc` menu, again to quit. Two tanks, three symmetric fields, one shot in flight
each, 2:16 on the clock — a hit sends both tanks back to their starting corners.
