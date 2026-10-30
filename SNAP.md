# Publishing the Snap Store package

`snap/snapcraft.yaml` builds `hydromancer-mcp` as a strictly confined snap (base `core24`, Python plugin, `network` plug only). This file lists the one-time setup and the steps for each release. Snaps run on Linux only.

## What the snap does at runtime

- **Environment.** snapd starts from the caller's environment, adds or replaces the snap runtime variables (`SNAP_*`, `HOME` set to `$SNAP_USER_DATA`, `XDG_RUNTIME_DIR`, `PATH`), overlays the variables set in `snapcraft.yaml`, and strips unsafe loader variables ([snapd source](https://github.com/canonical/snapd/blob/master/snap/snapenv/snapenv.go)). Ordinary custom variables are kept, so `HYDROMANCER_API_KEY`, `HYDROMANCER_BASE_URL` and the other settings passed by an MCP client's `env` block reach the server as set.
- **Files.** The server writes no files. `PYTHONDONTWRITEBYTECODE=1` stops Python from trying to write `.pyc` files into the read-only snap. Strict confinement would also block writes outside `$SNAP_USER_DATA`.
- **Network.** The `network` plug (auto-connected) allows outgoing connections. The server itself only contacts `api.hydromancer.xyz` (or `api-testnet.hydromancer.xyz`), and the code refuses any other host. No other interfaces are requested.

## One-time setup

1. **Company Ubuntu One account.** Create it with a company address (not a personal one) at <https://login.ubuntu.com>. The same account is used for the Snap Store and Launchpad.
2. **Snap Store developer account.** Sign in at <https://snapcraft.io/account> with that account, accept the developer terms, and set the publisher display name to "Hydromancer".
3. **snapcraft.** Use the 8.x track, which these steps were written against. Either:
   - on macOS, with Multipass as the build provider:
     ```bash
     brew install --cask multipass
     brew install snapcraft
     ```
     then run the commands below from the repository folder on the Mac, or
   - on Linux:
     ```bash
     sudo snap install snapcraft --classic --channel=8.x/stable
     sudo snap install lxd && sudo lxd init --auto && sudo usermod -aG lxd "$USER"   # log out and back in
     ```
4. **Log in and register the name** (from the snapcraft machine):
   ```bash
   snapcraft login
   snapcraft register hydromancer-mcp
   ```
5. **Store listing.** At <https://snapcraft.io/hydromancer-mcp/listing>, check the title, summary, description and icon (taken from `snapcraft.yaml` on first upload), set the category to "Development", and fill in the contact, issues and source-code links. These are placeholders in `snapcraft.yaml` until the public repository exists.

## Each release

1. **Bump the version** in both `src/hydromancer_mcp/__init__.py` and `snap/snapcraft.yaml` (a unit test fails if they differ). Run `uv run pytest`.
2. **Build for amd64 and arm64.** `snapcraft remote-build` builds every platform in `snapcraft.yaml` on Launchpad, using the same Ubuntu One account:
   ```bash
   snapcraft remote-build --launchpad-accept-public-upload
   ```
   This uploads the project source to a public Launchpad build, so only run it once the source repository is public. To build locally instead, run `snapcraft pack`. That builds only the host's architecture (arm64 on Apple Silicon).
3. **Test the built snap** on a Linux machine (or in a Multipass VM: `multipass launch 24.04 --name snaptest`, then copy the `.snap` in with `multipass transfer`):
   ```bash
   sudo snap install --dangerous ./hydromancer-mcp_0.1.0_arm64.snap
   hydromancer-mcp --version
   HYDROMANCER_API_KEY=test-value snap run --shell hydromancer-mcp   # opens a shell inside the snap's environment
   env | grep HYDROMANCER_API_KEY; exit                               # should print test-value
   HYDROMANCER_API_KEY=YOUR_KEY python3 scripts/smoke_stdio.py    # needs `pip install mcp`; uses the installed hydromancer-mcp
   ls ~/snap/hydromancer-mcp/current/                               # should be empty: nothing written
   ```
4. **Upload and release:**
   ```bash
   snapcraft upload --release=stable hydromancer-mcp_0.1.0_amd64.snap
   snapcraft upload --release=stable hydromancer-mcp_0.1.0_arm64.snap
   ```
   For a trial run, upload both files with `--release=edge` first, then promote both architectures at once with `snapcraft promote hydromancer-mcp --from-channel=edge --to-channel=stable`.

## Using the snap

```bash
sudo snap install hydromancer-mcp
claude mcp add hydromancer -e HYDROMANCER_API_KEY=YOUR_KEY -- hydromancer-mcp
```

In JSON or TOML configs (Cursor, Codex), use `"command": "/snap/bin/hydromancer-mcp"` with no `args`, plus the same `env` block as in the README.

## Optional: publish from CI

To publish from GitHub Actions instead of a workstation, create a store credential limited to this snap:

```bash
snapcraft export-login --snaps hydromancer-mcp --acls package_access,package_push,package_update,package_release --channels stable,edge -
```

Then store it as the `SNAPCRAFT_STORE_CREDENTIALS` repository secret and use `snapcore/action-build` and `snapcore/action-publish`.
