# Code Signing + AV False-Positive Mitigation

Everything about signing Windows executables and reducing AV false positives for PyInstaller-bundled apps.

## Rule 34: PyInstaller `--onefile` triggers antivirus false positives

`--onefile` compresses the executable in a way that triggers Windows Defender and other AV as "suspicious." Mitigations:

1. **Code signing** (see Rule 39) — builds reputation over time
2. **Use `--onedir` for enterprise distribution** — less compression, fewer triggers
3. **Submit to AV vendors** for whitelisting (see Rule 51)
4. **Add exclusion instructions** in your deployment guide for IT teams

This doesn't break functionality but blocks first-run on managed Windows machines.

## Rule 39: Sign the final binary with SHA-256 + a timestamp server

Unsigned Windows executables trigger SmartScreen warnings (`"Windows protected your PC"`) and Defender quarantines. For any external distribution, sign the exe. Use SHA-256 and always include `/tr` (RFC 3161 timestamp) — without a timestamp, signatures expire when the cert expires; with one, the signed binary stays valid forever.

```bat
REM Run after PyInstaller finishes (dist/MyApp/MyApp.exe or dist/MyApp.exe)
signtool sign ^
  /fd SHA256 ^
  /a ^
  /tr http://timestamp.digicert.com ^
  /td SHA256 ^
  dist\MyApp\MyApp.exe

REM Verify the signature
signtool verify /pa /v dist\MyApp\MyApp.exe
```

Public timestamp servers (free, rotate on failure):
- `http://timestamp.digicert.com`
- `http://timestamp.sectigo.com`
- `http://time.certum.pl`

Key flags:
- `/fd SHA256` — file digest (SHA1 is deprecated, Windows rejects it)
- `/td SHA256` — timestamp digest (must match `/fd`)
- `/a` — auto-select best cert from Windows cert store
- `/tr` — RFC 3161 timestamp server (use this, NOT the older `/t`)

If PyInstaller produces a one-directory build (recommended), sign **MyApp.exe** inside `dist/MyApp/`, not the whole folder.

## Rule 40: OV vs EV vs Azure Trusted Signing — updated SmartScreen reality (2024+)

**IMPORTANT 2024 change:** EV certificates no longer grant instant SmartScreen immunity. Microsoft removed all EV Code Signing OIDs from the Trusted Root Program in August 2024; prior to that, EV bypassed SmartScreen instantly but that's no longer true. Both OV and EV certs now build reputation organically via download volume + time. Expect 2-4 week ramp-up regardless of cert type.

Additionally, CA/Browser Forum rules (June 2023+) now require all code-signing private keys to live on FIPS 140-2 Level 2+ hardware — no more `.pfx` files on disk. "Cert-in-software-file" workflows are non-compliant.

| Option | 2026 Cost | Hardware needed | SmartScreen outcome | CI runner needs |
|---|---|---|---|---|
| **Self-signed** | Free | None | Blocked (no trust chain) | N/A |
| **OV cert + YubiKey** | $200-400/yr + $100 token | YubiKey 5 FIPS / eToken | Organic reputation ramp | Self-hosted (USB access) |
| **EV cert + YubiKey** | $300-600/yr + $100 token | YubiKey 5 FIPS / eToken | Organic reputation ramp (same as OV post-Aug 2024) | Self-hosted (USB access) |
| **Azure Trusted Signing** | ~$120/yr ($10/mo Basic) | None (cloud HSM) | Organic reputation ramp | Cloud-hosted via OIDC federation |
| **SignPath Foundation** | Free | None | Same as OV | OSS projects only (proprietary ineligible) |

**Recommendation for small-distribution commercial tools:** Azure Trusted Signing. Lowest cost, no hardware procurement, works with stock GitHub-hosted runners via OIDC (no secrets in repo), SmartScreen outcome identical to OV.

Detail: Azure Trusted Signing individual certs are only valid for 3 days, but the RFC 3161 timestamp countersignature keeps signatures valid indefinitely after the fact. You get a fresh cert per signing call; customers never see a 3-day cert.

**If choosing a hardware-token path:** EV costs more than OV without the historic SmartScreen benefit. For internal distribution, OV + YubiKey is the cheaper equivalent. EV only makes sense if driver signing or specific enterprise compliance requires it.

## Rule 41: Unblock workaround for unsigned / unreputable binaries

Until reputation builds, users on Windows 10/11 with SmartScreen enabled see "Windows protected your PC" on first run. They can bypass it:

1. Download the zip / exe
2. Right-click the downloaded file → **Properties**
3. Check **Unblock** at the bottom of the General tab
4. Click OK → run normally

Windows stores a "Zone.Identifier" alternate data stream on files from the internet. Unblock removes it. Document this in your release notes:

```markdown
## First-Run on Windows

If SmartScreen blocks the installer:
1. Right-click the .exe → Properties
2. Check "Unblock" → OK
3. Run the installer
```

Alternatively, ship via a network share or copy over SSH — files that never touched an "internet zone" don't get the marker.

## Rule 42: Verify signature in CI before publishing

A PyInstaller build can succeed with an invalid signature (expired cert, bad timestamp). Always `signtool verify /pa` in CI and fail the pipeline on nonzero exit.

```yaml
- name: Sign release binary
  run: |
    signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 `
      dist\GudeDeploy\GudeDeploy.exe
    signtool verify /pa /v dist\GudeDeploy\GudeDeploy.exe
  shell: pwsh

- name: Fail on unsigned
  if: failure()
  run: exit 1
```

`/pa` uses "Default Authentication Verification Policy" — the right flag for end-user distribution signing. Don't use `/r` unless doing driver signing.

## Rule 50: Ship via Inno Setup installer on top of `--onedir`, not bare .exe

Standalone PyInstaller `.exe` files have 30-50% AV flag rates across vendors; the same payload wrapped in a recognized installer format (Inno Setup, NSIS, WiX) drops to 10-20%. Reason: AV heuristics treat the Windows Installer path as trusted context, and the installed files end up in `Program Files` rather than a temp extraction directory.

```bat
REM 1) PyInstaller one-directory build
pyinstaller gude-deploy.spec

REM 2) Wrap dist\GudeDeploy\ with Inno Setup (free, single .iss file)
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" packaging\gude-deploy.iss

REM 3) Sign the installer .exe (not just the inner app .exe)
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 ^
    dist\GudeDeploy-Setup-%VERSION%.exe
```

Sign BOTH the payload `GudeDeploy.exe` AND the installer `GudeDeploy-Setup.exe`. The installer is what customers download and run first — the one that needs the best reputation.

## Rule 51: Submit false positives directly to AV vendors; VirusTotal is NOT a whitelist channel

Widely repeated folklore: "upload to VirusTotal and Microsoft will auto-whitelist." **This is false.** VirusTotal is a test aggregator — uploading there exposes the hash to 70+ AV engines for analysis, often *increasing* short-term detection rates. Microsoft (and every other vendor) requires submission to their own portal:

| Vendor | Portal | Response time |
|---|---|---|
| Microsoft Defender | https://www.microsoft.com/en-us/wdsi/filesubmission | 24-48h |
| Avast / AVG | https://www.avast.com/false-positive-file-form.php | 48-72h |
| Kaspersky | https://support.kaspersky.com/general/true-false | ~1 week |
| ESET | https://support.eset.com/en/kb141 | 2-3 days |
| Bitdefender | https://www.bitdefender.com/support/submit-sample/ | ~1 week |
| McAfee | https://www.mcafee.com/en-us/threat-center/submit-suspicious-file.html | ~5 days |
| Symantec/Norton | https://submit.symantec.com/ | varies |

Submissions whitelist by hash (per-build, temporary) unless you provide a cert — a cert whitelist covers every build signed with that cert (permanent). Always submit the SIGNED binary.

For each submission include: SHA-256 hash, cert fingerprint, a one-line justification (`"Commercial commissioning tool for GUDE Power PDUs, internal customer deployment"`), and a link to your company site. AV vendors favor submissions with institutional context.

## Rule 52: Rebuilding the PyInstaller bootloader is a tier-3 fix, not a default

Common recommendation: "rebuild PyInstaller's bootloader from source to break AV signature matching." **It's real but limited.** Measured impact:

- Heuristic/signature AV (Kaspersky, BitDefender): 20-40% FP reduction
- Windows Defender (behavior-based): < 5% additional benefit beyond code signing
- Third-party AV that specifically fingerprints the stock bootloader: significant relief

Only do this if: (a) you're already code-signing, (b) you've already distributed via installer (Rule 50), (c) you've already submitted FP reports (Rule 51), AND (d) a specific third-party AV is still flagging in customer environments. It costs ~30-60 minutes per build, requires a C++ toolchain (MSVC), and the benefit is per-build (every rebuild produces a different binary).

```bash
# Only after tiers 1-3 exhausted:
pip uninstall pyinstaller
git clone --branch v6.11.0 https://github.com/pyinstaller/pyinstaller.git
cd pyinstaller/bootloader
python waf distclean configure --msvc_version=msvc_latest --arch=x86_64 build
cd .. && pip install -e .
```

Never self-sign, strip imports, rename `python311.dll`, or UPX-compress as AV evasion — those are all either folklore or actively counterproductive.
