---
id: 2026-02-27-multiplivault-echidna-fuzzing-skill-inst-0e0554c9
date: 2026-02-27
project: MultipliVault
topic: echidna-fuzzing-bounty
tags: [echidna, fuzzing, solidity, erc4626, bug-bounty, hackenproof, avalanche, smart-contracts]
source: claude-code-session
confidence: n/a
---

## MultipliVault Echidna Fuzzing & Skill Installation Session

### Context
Bug bounty on HackenProof targeting Multipli smart contracts on Avalanche. Rewards: Critical $5k-$10k, High $2k-$5k, Medium $750-$2k, Low $0-$750.

### Echidna Setup & Debugging
Extensive debugging of Echidna deployment issues for continuous fuzzing of MultipliVault:

1. **"invalid b16 decoding"** — Echidna expects raw hex without `0x` prefix. Fixed via `sed 's/^0x//'`.
2. **Contract deployment failure** — Used runtime bytecode instead of creation bytecode. Fixed by switching to `forge inspect TestableVault bytecode`.
3. **Silent deployment failure** — `balanceAddr`/`balanceContract` hex values in YAML caused failure. Removed entirely (root cause found via incremental config testing).
4. **OwnableUnauthorizedAccount errors** — `VariableVaultFee.registerAsset()` and `MockERC20.mint()` failed due to ownership. Fixed by making EchidnaSetupLight self-owned during setup, then transferring ownership.
5. **testLimit: 0 means zero tests, not unlimited** — Changed to `testLimit: 500000`.

### Fuzzing Results

**First run (6 strict properties):** 5 of 6 failed — all caused by `adminMint` creating unbacked shares. These were false positives since adminMint intentionally breaks accounting.

**Second run (13 realistic properties, 500K calls):** 9 passing, 4 failed:
- `echidna_share_accounting` — ghost variable tracking bug in the harness itself
- `echidna_no_zero_share_for_real_deposit` — donation inflation attack requiring ~10B USDC (not practical)
- `echidna_exchange_rate_ge_one` — off-by-1 rounding after oracle update (expected ERC4626 behavior)
- `echidna_mint_deposit_inverse` — previewMint rounds UP, previewDeposit rounds DOWN (expected ERC4626 convention)

### Rounding Analysis
All 3 rounding findings are expected ERC4626 behavior:
- **Exchange rate off-by-1**: After oracle updates totalAssets, `convertToAssets(totalSupply)` can be 1 wei less than totalAssets due to integer division truncation
- **Mint/deposit asymmetry**: `previewMint` uses `mulDivUp` while `previewDeposit` uses `mulDivDown` — both favor the vault, which is correct
- **Donation inflation**: First depositor attack requires mass of ~10B USDC to steal meaningful amounts from a 1 USDC deposit

### Key Files
- `test/echidna/EchidnaMultipliVault.sol` — Main fuzzing harness with 13 realistic properties
- `test/echidna/EchidnaSetup.sol` — Setup helper with ownership fixes
- `test/echidna/TestableVault.sol` — MultipliVault without `_disableInitializers()`
- `echidna.yaml` — Working Echidna config (property mode, 500K calls, 4 workers)

### Skill Installation
Installed two skills from github.com/linkeLi0421/my-skills:
- `~/.claude/skills/summarize-to-notes/` — Summarizes text into structured Markdown notes
- `~/.claude/skills/git-sync-notes/` — Syncs notes git repo (pull --rebase, add, commit, push)

Both have `DEFAULT_NOTES_REPO_PATH` placeholder that needs replacing or passing `repo_path` in input.

### Echidna Config (Final Working)
```yaml
testMode: "property"
testLimit: 500000
shrinkLimit: 5000
seqLen: 100
deployer: "0x10000"
sender: ["0x10000", "0x20000", "0x30000"]
corpusDir: "echidna-corpus"
codeSize: 0xFFFFFF
workers: 4
```

### Run Command
```bash
echidna test/echidna/EchidnaMultipliVault.sol --contract EchidnaMultipliVault --config echidna.yaml
```
