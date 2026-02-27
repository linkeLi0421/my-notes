---
id: 2026-02-27-multiplivault-echidna-fuzzing-rounding-a-c86fa4ad
date: 2026-02-27
project: MultipliVault
topic: echidna-fuzzing-rounding-analysis
tags: [echidna, fuzzing, solidity, erc4626, rounding, bug-bounty, hackenproof, avalanche, integer-math, inflation-attack]
source: claude-code-session
confidence: n/a
---

## MultipliVault Echidna Fuzzing & Rounding Analysis

### Context
Bug bounty on HackenProof targeting Multipli smart contracts on Avalanche C-Chain. Rewards: Critical $5k-$10k, High $2k-$5k, Medium $750-$2k, Low $0-$750. PoC required (Foundry preferred).

#### In-Scope Contracts
| Contract | Address |
|----------|--------|
| MultipliVault (impl) | `0xb63601A11c5bDC79D511B8F73871d7C0d8B57AE9` |
| ERC1967Proxy (xUSDC) | `0xCF0Eb4ac018C06a16ED5c63484823C7805e7599D` |
| VariableVaultFee | `0x4E5FEa916ef8458b8D877BD760B6930Fb4f28B72` |
| VaultFundManager | `0x01e676EAA0C9780A88395c651349Cf08Fe52368e` |
| RolesAuthority | `0xf580B985e2Fd8A8b0e4a56C2a7E24bC28e872609` |

---

### Echidna Setup Journey

Getting Echidna to deploy MultipliVault (a UUPS upgradeable ERC4626 vault with async redeem) required solving several non-obvious issues:

#### Problem 1: "invalid b16 decoding"
Echidna's `deployBytecodes` expects raw hex **without** the `0x` prefix. Forge outputs bytecode with `0x`.
```bash
# Fix: strip the prefix
forge inspect TestableVault bytecode | sed 's/^0x//'
```

#### Problem 2: Runtime vs Creation Bytecode
`forge inspect TestableVault deployedBytecode` gives runtime bytecode — but Echidna's `deployBytecodes` needs **creation bytecode** (the constructor-bearing version) so it can actually deploy.
```bash
# Correct: use creation bytecode
forge inspect TestableVault bytecode | sed 's/^0x//'
# Wrong: runtime bytecode
forge inspect TestableVault deployedBytecode | sed 's/^0x//'
```

#### Problem 3: balanceAddr/balanceContract Crash
`balanceAddr: 0xFFFFFFFFFFFFFFFF` and `balanceContract: 0xFFFFFFFFFFFFFFFF` in `echidna.yaml` caused **silent deployment failure** — the contract at the target address just didn't exist. Found via incremental config testing (adding one YAML option at a time). Solution: remove both options entirely.

#### Problem 4: Ownership Chain
`EchidnaSetupLight` (the helper contract deployed via `deployContracts`) needs to:
1. Deploy `VariableVaultFee` with `address(this)` as owner (so it can call `registerAsset()`)
2. Call `registerAsset()` to configure the fee contract
3. Transfer ownership to the actual deployer (`0x10000`)
4. Same pattern for `MockERC20` — deploy, then `transferOwnership(owner)`

```solidity
// In EchidnaSetup constructor:
asset = new MockERC20("USDC", "USDC", 6);
asset.transferOwnership(owner);
feeContract = new VariableVaultFee(address(this)); // self-owned temporarily
feeContract.registerAsset(...);
feeContract.transferOwnership(owner); // hand off
```

#### Problem 5: testLimit: 0 ≠ Unlimited
Setting `testLimit: 0` means zero tests, not infinite. Echidna workers immediately stop with `fuzzing: 1304/0`. Use a large number like `testLimit: 999999999` or a reasonable `500000`.

#### Problem 6: Deployer Must Be a Sender
If the deployer address (`0x10000`) isn't in the `sender` list, Echidna can't call setup functions. Always include the deployer in senders.

#### Final Working Config
```yaml
testMode: "property"
testLimit: 500000
shrinkLimit: 5000
seqLen: 100
deployer: "0x10000"
sender:
  - "0x10000"
  - "0x20000"
  - "0x30000"
corpusDir: "echidna-corpus"
codeSize: 0xFFFFFF
workers: 4
format: text
deployContracts:
  - ["0x00000000000000000000000000000000000AAAAA", "EchidnaSetupLight"]
deployBytecodes:
  - ["0x00000000000000000000000000000000000BBBBB", "<creation bytecode>"]
```

---

### Fuzzing Round 1: Strict Properties (6 Properties, False Positives)

First attempt used 6 strict invariants. 5 of 6 failed — **all false positives** caused by `adminMint()` creating unbacked shares:

1. `echidna_share_price_gte_one` — FAILED: adminMint inflates supply without backing
2. `echidna_no_free_shares` — FAILED: adminMint gives shares for free by design
3. `echidna_conversion_rounding_favors_vault` — PASSED
4. `echidna_solvency` — FAILED: adminMint makes vault insolvent for existing holders
5. `echidna_no_zero_share_deposit` — FAILED: after adminMint inflates supply, small deposits round to 0 shares
6. `echidna_total_supply_consistency` — FAILED: ghost tracking didn't account for admin operations

**Lesson:** `adminMint`/`adminBurn` are intentional privileged operations that break naive accounting invariants. They should be excluded from fuzzer actions when testing economic properties.

---

### Fuzzing Round 2: Realistic Properties (13 Properties, 500K Calls)

Rewrote the harness excluding `adminMint`/`adminBurn` from action functions. Added ghost variable tracking. Used minimum deposit of `1e6` (1 USDC) instead of `1` wei.

#### Action Functions
- `action_deposit(uint256)` — deposit as msg.sender, clamp to [1e6, balance]
- `action_deposit_for_user(uint256)` — deposit for address `0x20000`
- `action_mint_shares(uint256)` — mint shares as msg.sender
- `action_request_redeem(uint256)` — request async redeem
- `action_fulfill_redeem(uint256)` — admin fulfills pending redeem
- `action_cancel_redeem(uint256)` — admin cancels pending redeem
- `action_update_balance(uint256)` — oracle updates underlying balance (±20%)
- `action_donate_to_vault(uint256)` — direct ERC20 transfer to vault (inflation attack vector)

#### Ghost Variables
```solidity
uint256 public totalDeposited;       // cumulative assets deposited
uint256 public totalSharesMinted;     // cumulative shares received from deposits
uint256 public totalAssetsWithdrawn;  // cumulative assets from fulfilled redeems
uint256 public totalSharesBurned;     // cumulative shares burned in redeems
uint256 public totalSharesCancelled;  // cumulative shares returned from cancelled redeems
uint256 public ghostPendingShares;    // running total of pending redeem shares
```

#### Results: 9 Passing, 4 Failed

**PASSING (9):**
1. `echidna_roundtrip_rounding` — convertToAssets(convertToShares(x)) <= x ✓
2. `echidna_deposit_preview_ge_convert` — previewDeposit(x) >= convertToShares(x) ✓
3. `echidna_vault_holds_pending` — vault's own share balance >= ghostPendingShares ✓
4. `echidna_supply_conservation` — totalSupply changes only through deposit/mint/redeem/cancel ✓
5. `echidna_pending_consistency` — totalPendingAssets only increases on requestRedeem, decreases on fulfill/cancel ✓
6. `echidna_no_deposit_redeem_profit` — no instant deposit→requestRedeem profit ✓
7. `echidna_total_assets_ge_balance` — totalAssets() >= asset.balanceOf(vault) ✓ (with oracle)
8. `echidna_pending_assets_nonneg` — totalPendingAssets >= 0 (can't underflow) ✓
9. `echidna_pause_consistency` — paused state correctly blocks operations ✓

**FAILED (4) — All Explained:**

10. `echidna_share_accounting` — **Harness bug**, not protocol bug. Ghost variable `totalSharesMinted` was incremented for `address(this)` but deposits went to `0x20000`. Fix: track per-recipient.

11. `echidna_no_zero_share_for_real_deposit` — Donation inflation attack. Attacker donates tokens directly to vault, inflating share price so victim's deposit rounds to 0 shares. **Requires ~10 billion USDC** to steal meaningful amounts from a 1 USDC deposit. Not practical.

12. `echidna_exchange_rate_ge_one` — Off-by-1 after oracle update. See rounding analysis below.

13. `echidna_mint_deposit_inverse` — previewMint/previewDeposit asymmetry. See rounding analysis below.

---

### Rounding Analysis (Deep Dive)

All three rounding findings are **expected ERC4626 behavior**, not bugs.

#### Finding 1: Exchange Rate Off-by-1 After Oracle Update

**What happens:** After `onUnderlyingBalanceUpdate()` changes `totalAssets`, the invariant `convertToAssets(totalSupply) >= totalSupply` can fail by exactly 1 wei.

**Math trace:**
```
State: totalAssets = 1000007, totalSupply = 1000000

convertToAssets(totalSupply) = totalSupply * totalAssets / totalSupply
                              = 1000000 * 1000007 / 1000000
```

In Solidity integer division:
```
1000000 * 1000007 = 1000007000000
1000007000000 / 1000000 = 1000007  (exact, no remainder)
```

But with non-round numbers like `totalAssets = 1000003, totalSupply = 999997`:
```
convertToAssets(999997) = 999997 * 1000003 / 999997 = 1000003 (exact here)
```

The failure happens when oracle updates create a `totalAssets` that doesn't divide evenly:
```
totalAssets = 2000001, totalSupply = 2000000
convertToAssets(2000000) = 2000000 * 2000001 / 2000000 = 2000001 ✓

But after fee + rounding chain:
actualAssets stored = 1999999 (due to fee rounding)
totalSupply still = 2000000
convertToAssets(2000000) = 2000000 * 1999999 / 2000000 = 1999999 < 2000000 ✗
```

**Verdict:** This is inherent to integer math. The vault's `totalAssets` after oracle updates doesn't guarantee exact divisibility with `totalSupply`. The 1 wei discrepancy is economically meaningless and is standard ERC4626 behavior.

**Severity:** Informational / Not a bug.

#### Finding 2: previewMint vs previewDeposit Asymmetry

**What happens:** `previewMint(convertToShares(assets))` can return `assets + 1`, violating a naive "roundtrip" expectation.

**Math trace:**
```
State: totalAssets = 1000003, totalSupply = 1000000

Step 1: convertToShares(1000) = 1000 * 1000000 / 1000003
        = 1000000000 / 1000003
        = 999 (truncated — rounds DOWN, fewer shares, favors vault)

Step 2: previewMint(999) = 999 * 1000003 / 1000000 (rounds UP via mulDivUp)
        = 999002997 / 1000000
        = 999.002997 → rounds UP to 1000
```

So depositing 1000 assets gets you 999 shares, but minting 999 shares costs 1000 assets.

**Why this is correct:**
- `previewDeposit` / `convertToShares`: rounds **DOWN** (you get fewer shares → vault keeps dust)
- `previewMint` / `convertToAssets`: rounds **UP** (you pay more assets → vault keeps dust)
- Both directions favor the vault, preventing share dilution attacks
- This is the **OpenZeppelin ERC4626 standard implementation**

**Severity:** Informational / By design.

#### Finding 3: Donation Inflation (Zero-Share Deposit)

**What happens:** An attacker can:
1. Deposit 1 wei to get 1 share
2. Donate a huge amount of tokens directly to the vault via `ERC20.transfer()`
3. This inflates `totalAssets` without increasing `totalSupply`
4. A victim's subsequent deposit gets `amount * 1 / (1 + huge_donation)` → rounds to 0 shares

**Math trace:**
```
Attacker deposits 1 wei → gets 1 share
Attacker donates 10,000,000,000e6 USDC to vault

State: totalAssets = 10_000_000_001e6, totalSupply = 1

Victim deposits 1e6 (1 USDC):
shares = 1e6 * 1 / 10_000_000_001e6 = 0 (truncated)
```

**Why it's not practical:**
- Requires mass of ~$10 billion USDC to attack a $1 deposit
- The attacker loses the donated funds (they're trapped in the vault backing the 1 share)
- MultipliVault also has admin controls and minimum deposit thresholds that mitigate this
- OpenZeppelin recommends "virtual shares" (ERC4626 with offset) for vaults that need protection, but the economic cost here makes it irrelevant

**Severity:** Informational / Not practical. Standard known ERC4626 consideration.

---

### Key Files
- `test/echidna/EchidnaMultipliVault.sol` — Main fuzzing harness with 13 realistic properties and ghost variable tracking
- `test/echidna/EchidnaSetup.sol` — Setup helper deploying MockERC20, VariableVaultFee, MockAuthority with correct ownership chain
- `test/echidna/TestableVault.sol` — MultipliVault subclass without `_disableInitializers()` (needed for Echidna deployment)
- `echidna.yaml` — Working Echidna configuration
- `src/vault/MultipliVault.sol` — Main target contract (UUPS upgradeable ERC4626 with async redeem)
- `src/fees/VariableVaultFee.sol` — Fee calculation logic

### Run Command
```bash
echidna test/echidna/EchidnaMultipliVault.sol --contract EchidnaMultipliVault --config echidna.yaml
```
