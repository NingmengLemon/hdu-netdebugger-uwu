# pyside6-desktop

Production rules for building PySide6 (or PyQt6) desktop applications. QSS styling bugs, QThread safety, PyInstaller packaging, Windows code signing, AV false-positive mitigation.

52 rules total, split into a brief main SKILL.md (~1.7k tokens) and 7 reference files loaded on demand. Drawn from shipping a commissioning tool to 120+ devices.

## When to use

- You're writing PySide6 or PyQt6 code
- You're editing a Qt stylesheet (`.qss`)
- You're tweaking a PyInstaller `.spec` file
- You're signing a Windows `.exe` with `signtool`
- You've hit "invisible text on Windows dark mode" and want to know why
- You're seeing Windows Defender flag a PyInstaller build and want the layered mitigation

## When NOT to use

- You're doing server-side Python (no Qt involved)
- You're packaging a library for PyPI (use a packaging-focused skill instead)

## What's notable

This is the skill we refactored from a 936-line monolith into 168-line main + 7 references during the `skill-forge` v0.1.0 session. Original token cost on invocation: ~9,200. Post-refactor: ~1,700. Same content, 5.4× less context pressure.

The progressive-disclosure structure is the worked example for how `/skill-forge`'s Phase 7 handles oversized skills. The rules inside (1-52) cover real production bugs with one-sentence fixes.

## Reference files

| File | Topic |
|---|---|
| `references/qss.md` | QSS styling rules — the "invisible text" bug class |
| `references/widgets.md` | Visibility traps with `setVisible()` and reset() |
| `references/threading-and-cleanup.md` | QThread safety, signal/slot rules, `deleteLater`, lambdas |
| `references/pyinstaller.md` | `.spec` file patterns, hiddenimports, UPX, PyInstaller 6.x edges |
| `references/platform-gotchas.md` | Windows-specific: netifaces leak, DPI scaling, ARM64 |
| `references/signing-and-av.md` | signtool, Azure Trusted Signing, SmartScreen, Inno Setup, FP mitigation |
| `references/architecture.md` | Wizard-page flow, Pydantic v2 data layer, force_field pattern |

## Install

```bash
# From the claude-code-skills repo root:
./install.sh pyside6-desktop

# Or manually:
cp -r pyside6-desktop ~/.claude/skills/
```

## Audited by skill-forge

Every rule cites a source — either an authoritative URL (PyInstaller changelog, Microsoft Learn, Qt docs) or a project-memory reference from the production deployment. Structural audit (YAML, description length, rule coverage, reference orphans, progressive-disclosure balance) passes clean.

To tune your own PySide6 skill the same way: [install skill-forge](https://github.com/NerdBase-by-Stark/skill-forge) and run `/skill-forge` in your project.
