# HOOD's Linux sandbox on Windows (WSL2)

Agent-written code (Python programs' tests, WordPress sites) must run somewhere isolated. On Windows,
HOOD sets up and manages **its own Linux sandbox** with WSL2. You only approve; HOOD does every step.

| Mission kind | Needs the sandbox? |
|---|---|
| Website (HTML/CSS/JS) | no: checked by reading the files, nothing is run |
| Python program | yes (or your per-mission "Run on my PC" approval) |
| WordPress site | yes: PHP and WordPress are installed inside it |

## What you do

Press **Allow & set up** once. It appears where it's needed: on a mission that waits for the sandbox,
next to a WordPress mission's tools (one approval covers the sandbox *and* the tools), and in
**Settings › Agents**. That's all. Missions that were waiting continue by themselves when the sandbox
is ready.

Only Windows itself can ask two more things of you, and HOOD tells you before you approve:

1. **If WSL isn't installed yet**, Windows shows its administrator prompt ("Do you want to allow this
   app to make changes?"): click **Yes**.
2. Windows may then **need a restart**. HOOD shows a **Restart now** button (Windows restarts after
   60 seconds), or restart whenever you like. When you start HOOD again, it finishes the setup by
   itself and continues the waiting missions and installs.

If WSL is already installed, there is no prompt and no restart.

## What HOOD does after your OK

1. Installs WSL if it's missing (`wsl --install --no-distribution`, through Windows' prompt above).
2. Downloads Ubuntu 24.04's official WSL image (about 340 MB) from `cloud-images.ubuntu.com` through
   HOOD's egress firewall (your OK allows that host) and checks it against Ubuntu's published SHA-256.
   An image that doesn't match is never used.
3. Imports it as a **separate distro named `HOOD`** (in HOOD's data folder). Your own WSL distros are
   not touched.
4. Locks it down: Windows interop off (code there can't start Windows programs), Windows `PATH` not
   added.
5. Installs Python 3 and pytest there from Ubuntu's signed packages, then checks that isolated runs
   work.

Every agent run then happens inside that distro, in a fresh namespace: only the mission folder is
visible (writable) plus HOOD's own launcher (read-only); **all Windows drives are hidden**; there is
**no network** (loopback only); CPU, memory, file size and open files are limited. Tools you allow
(PHP for WordPress, …) are installed inside the same distro, never on Windows.

**Emergency STOP** also stops everything running in the `HOOD` distro.

## What only you can fix (HOOD says so in plain words)

| HOOD says | Why | What to do |
|---|---|---|
| "Virtualization is switched off in this PC's firmware" | WSL2 needs CPU virtualization, which is a BIOS/UEFI setting no program can change | restart into the BIOS/UEFI setup, enable Intel VT-x / AMD SVM ("Virtualization Technology"), save, then **Try again** in HOOD |
| "The administrator prompt was declined" | you clicked No (nothing was changed) | **Try again** and click Yes |
| "The disk is full" | the sandbox needs about 2 GB | free some space, then **Try again** |
| "Windows couldn't download WSL" | no internet or a proxy blocks Microsoft's download | check the connection, then **Try again** |

## Remove it

`wsl --unregister HOOD` deletes HOOD's distro and everything in it (your own distros stay). HOOD asks
again before setting it up the next time.

## Optional: run HOOD itself inside WSL2

Not needed any more, but still supported (e.g. if you prefer HOOD's whole process in Linux). In a
normal PowerShell window, in the HOOD folder:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\hood-wsl.ps1
```
It copies HOOD into Ubuntu (`~/hood`), installs its Python packages, starts it and opens Chrome at
`http://127.0.0.1:8999`. There, HOOD uses Linux's own sandbox, and installs system packages through
WSL's root access (no password, no setup command). This is a separate HOOD: create the owner account
and save your model key again in Settings. Troubleshooting for this mode:

| What you see | Fix |
|---|---|
| `No Linux distribution is installed in WSL yet` | `scripts\windows\hood-wsl.ps1 -InstallWsl`, restart, open Ubuntu once |
| `Python 3.12 or 3.13 is missing` | in Ubuntu: `sudo apt update && sudo apt install -y python3 python3-venv` |
| Chrome cannot open `http://127.0.0.1:8999` | create `C:\Users\<you>\.wslconfig` with `[wsl2]` and `networkingMode=mirrored`, run `wsl --shutdown`, start again |
| Port already used | the Windows HOOD may still be running: stop it, or pass `-Port 8998` |

On a plain Linux server (not WSL, HOOD not running as root) HOOD has no way to install system
packages by itself; an administrator can allow its package helper once with
`sudo bash scripts/wsl/enable_installs.sh` (it accepts only the packages on HOOD's list).
