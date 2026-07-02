# Split plan: `storefront_ui.py` (1697 LOC) and `runtime_facade.py` (2375 LOC)

These two files are the largest in the repo and the hardest to change safely. This doc maps each into
proposed target modules with concrete function ranges, so the mechanical extraction can be executed as a
dedicated, well-tested branch (effort **L**, regression risk **high** — do NOT bundle it with other changes).

Why a plan rather than the extraction here: both files sit on the user-facing bot path (`/menu`, buy flow,
connect instructions, key reissue). A rushed 4000-LOC move alongside unrelated fixes is exactly how the bot
UI silently breaks. The extraction deserves its own branch with `test_bot_transport_*` green after every step.

## `bot_transport/storefront_ui.py` — 159 functions → 4 modules

Inventory by concern (line numbers are approximate anchors in the current file):

| Target module | Contents | Source functions |
|---|---|---|
| `storefront_ui_keyboards.py` | inline-keyboard builders + shared helpers | `_cb`, `_inline_kb`, `*_keyboard` (main_menu, buy_vpn, device_select, confirm_pay, back_only, no_subscription, settings, keys, my_keys_menu, single/all_keys_list, reissue_confirm, connect_*, welcome) + `CB_*` callback constants |
| `storefront_ui_texts.py` | message-text builders | `text_*` (welcome, main_menu, buy_vpn_intro, device_select, purchase_summary, payment_unavailable, subscription_active/expired/no_subscription, my_keys, reissue_confirm, connect_*) |
| `storefront_ui_connect.py` | connect-device instructions per platform (~half the file) | `ios_step_*`, `ios_did_work/success/problem*`, `mac_step_*`, `mac_did_work/success/problem*`, `tv_step_*`, `tv_*`, and the Android step builders; `text_connect_*`, `_platform_from_cb` |
| `storefront_ui.py` (residual) | public wiring + re-export shim | re-export the moved symbols so existing importers (`runtime_facade`, `dispatcher`) keep working unchanged |

Shared dependencies the extracted modules must import: `_cb`, `_inline_kb`, `CB_*` constants, and the
dataclasses (`PurchasePlanOption`, `PurchaseSummary`, `VlessUserConfig`, `VlessServerConfig`,
`DEFAULT_DEVICE_LIMIT`). Put those in `storefront_ui_keyboards.py` (or a tiny `_storefront_shared.py`) and
import from the other three.

## `bot_transport/runtime_facade.py` — 2375 LOC

Structure is unusual: a thin `Slice1TelegramRuntimeFacade` class near EOF with the bulk as module-level
functions. **Profile before splitting**: map which module-level functions belong to which concern
(billing/checkout orchestration, key lifecycle/reissue, presentation/render wiring, device management).
Likely seams: (a) checkout/payment summary building, (b) VLESS key render + reissue flow, (c) update-message
rendering. Extract one seam per commit behind the existing public methods.

## How to execute the split safely (per target module)

1. Create the new module; move the functions verbatim; add its imports.
2. In `storefront_ui.py`, **re-export** the moved names (`from storefront_ui_keyboards import *`-style or
   explicit) so no caller breaks. Keep the public surface identical.
3. Run `cd backend && python -m pytest -q tests/test_bot_transport_storefront_config.py
   tests/test_bot_transport_runtime_facade.py tests/test_bot_transport_presentation.py` after each move.
4. One module per commit; one concern per branch. Do not move runtime_facade and storefront_ui in the same PR.

## Tests to strengthen first (prerequisite)

`storefront_ui` has only `test_bot_transport_storefront_config.py` (config) — the text/keyboard builders are
lightly covered. Before moving them, add characterization tests over a sample of `text_*` / `*_keyboard`
outputs so the extraction is verified to preserve output byte-for-byte.
