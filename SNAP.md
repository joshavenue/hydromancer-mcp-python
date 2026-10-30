# Publishing the Snap Store package

`snap/snapcraft.yaml` builds `hydromancer-mcp` as a strictly confined snap (base `core24`, Python plugin, `network` plug only). This file lists the one-time setup and the steps for each release. Snaps run on Linux only.

## What the snap does at runtime

- **Environment.** snapd starts the app with the caller's environment and overrides only `SNAP_*`, `HOME` (set to `$SNAP_USER_DATA`), `XDG_RUNTIME_DIR` and `PATH` ([snapd source](https://github.com/canonical/snapd/blob/master/snap/snapenv/snapenv.go)). So `HYDROMANCER_API_KEY`, `HYDROMANCER_BASE_URL` and the other settings passed by an MCP client's `env` block reach the server unchanged.
- **Files.** The server writes no files. `PYTHONDONTWRITEBYTECODE=1` stops Python from trying to write `.pyc` files into the read-only snap. Nothing is written outside `$SNAP_USER_DATA`.
- **Network.** The `network` plug (auto-connected) allows outgoing HTTPS to `api.hydromancer.xyz` (or the testnet host). No other interfaces are requested.

## One-time setup

1. **Company Ubuntu One account.** Create it with a company address (not a personal one) at <https://login.ubuntu.com>. The same account is used for the Snap Store and Launchpad.
2. **Snap Store developer account.** Sign in at <https://snapcraft.io/account> with that account, accept the developer terms, and set the publisher display name to "Hydromancer".
3. **A machine with snapcraft.** Either:
   - a Linux machine: `sudo snap install snapcraft --classic`, or
   - Multipass on macOS:
     ```bash
     brew install --cask multipass
     multipass launch 24.04 --name snapbuild --disk 20G --memory 4G
     multipass exec snapbuild -- sudo snap install snapcraft --classic
     multipass shell snapbuild
     ```
     Inside the VM, clone the repository and `cd` into it.
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
   This uploads the project source to a public Launchpad build, so only run it once the source repository is public. To build locally instead, run `snapcraft` on the Linux machine or in the Multipass VM. That builds only the host's architecture (arm64 on Apple Silicon).
3. **Test the built snap** on a Linux machine or in the VM:
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
   For a trial run, use `--release=edge` first and promote it later with `snapcraft release hydromancer-mcp <revision> stable`.

## Using the snap

```bash
sudo snap install hydromancer-mcp
claude mcp add hydromancer -e HYDROMANCER_API_KEY=YOUR_KEY -- hydromancer-mcp
```

In JSON or TOML configs (Cursor, Codex), use `"command": "/snap/bin/hydromancer-mcp"` with no `args`, plus the same `env` block as in the README.

## Optional: publish from CI

To publish from GitHub Actions instead of a workstation, create a store credential limited to this snap:

```bash
snapcraft export-login --snaps hydromancer-mcp --acls package_upload,package_release --channels stable,edge -
```

Then store it as the `SNAPCRAFT_STORE_CREDENTIALS` repository secret and use `snapcore/action-build` and `snapcore/action-publish`.
