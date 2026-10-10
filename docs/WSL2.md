# Run HOOD in WSL2 (Windows)

On Windows, HOOD has no sandbox for agent-written code. That only matters for **Python
missions** (programs whose tests must run). Your options:

| Option | Website missions | Python missions | Safety |
|---|---|---|---|
| HOOD on Windows (default) | work: checked by reading the files, nothing is run | wait for your decision | nothing runs without you |
| HOOD on Windows + **Run on my PC** (Settings › Agents) | work | each mission asks you, then runs its fixed checks **directly on your PC** | no isolation: approve only code you are willing to run |
| **HOOD in WSL2** (this guide) | work | tests run in the Linux sandbox (no network, separate process group, limits) | recommended for Python missions |

WSL2 is Microsoft's Linux inside Windows. HOOD runs there; you keep using Chrome on Windows
at the same address, `http://127.0.0.1:8999`.

## 1. Install WSL2 (once)

1. Open **PowerShell as Administrator** (Start › type PowerShell › right-click › *Run as administrator*).
2. In the HOOD folder, run:
   ```powershell
   cd C:\Users\<you>\Documents\dracula
   powershell -ExecutionPolicy Bypass -File scripts\windows\hood-wsl.ps1 -InstallWsl
   ```
   (This runs `wsl --install -d Ubuntu-24.04`.)
3. Restart Windows. Open **Ubuntu** from the Start menu once and create your Linux user name and
   password (you will need that password only if a step asks for `sudo`).

## 2. Install and start HOOD in WSL2

In a normal PowerShell window, in the HOOD folder:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\hood-wsl.ps1
```
It copies the HOOD code into Linux (`~/hood`), installs the Python packages, checks the
sandbox, starts HOOD and opens Chrome at `http://127.0.0.1:8999`. Press **Ctrl+C** in that
window to stop HOOD. Run the same command again after you update HOOD: the code is refreshed,
your HOOD data (accounts, vault, missions) is kept.

First start: this is a separate HOOD from the Windows one. Create the owner account, then save
your Gemini key and prices again in **Settings › Model provider** (keys are never copied
between installations). Settings › Agents should show **Sandbox on this computer: available**.

## Let HOOD install what missions need (WordPress sites)

WordPress missions need PHP, WordPress, its SQLite plugin and WP-CLI. HOOD installs them **inside WSL2
only**, after you allow each tool once (it can reuse and update an allowed tool without asking again).
PHP comes from Ubuntu's packages, so switch on system installs once (your Linux password is asked by
`sudo`, never by HOOD):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\hood-wsl.ps1 -EnableInstalls
```
or, inside Ubuntu: `sudo bash ~/hood/scripts/wsl/enable_installs.sh`. This adds a small helper that can
only install the packages on HOOD's list (PHP, MariaDB, Node.js, Composer, SQLite) and nothing else.
Then, in HOOD: ask for a WordPress site in the chat (or **Settings › Tools**) and press **Allow & install**.
Undo: `sudo rm /usr/local/sbin/hood-pkg /etc/sudoers.d/hood-pkg`.

## Troubleshooting

| What you see | Fix |
|---|---|
| `No Linux distribution is installed in WSL yet` | step 1 |
| warning `runs on WSL1` | `wsl --set-version Ubuntu-24.04 2` |
| `Python 3.12 or 3.13 is missing` / `The venv module is missing` | in Ubuntu: `sudo apt update && sudo apt install -y python3 python3-venv`, then rerun step 2 |
| `NOT AVAILABLE: Python missions would stop before their checks` with an AppArmor note | in Ubuntu run the two `sudo` lines the script prints, then rerun step 2 |
| Chrome cannot open `http://127.0.0.1:8999` | wait until the window says `HOOD Interface running`; if it still fails, create `C:\Users\<you>\.wslconfig` containing `[wsl2]` and `networkingMode=mirrored`, run `wsl --shutdown`, then step 2 again |
| Port already used | the Windows HOOD may still be running: stop it (Ctrl+C), or pass `-Port 8998` |

Manual equivalent (inside Ubuntu):
```bash
bash /mnt/c/Users/<you>/Documents/dracula/scripts/wsl/install_hood_wsl.sh /mnt/c/Users/<you>/Documents/dracula --start
```
