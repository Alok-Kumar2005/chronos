# Lumen Store - Demo Shop with Time-Based Bugs

## Overview

Lumen Store is a realistic marketplace demo for testing the Chronos twin. It demonstrates how a typical developer would integrate PayPal for pre-orders (authorize now, capture on ship), with six deliberate time-based bugs that only appear when the virtual clock moves.

## What's Implemented

### ✅ Models and Database (backend/app/models.py)

7 new SQLAlchemy models:

| Model | Purpose |
|-------|---------|
| `ShopSettings` | Per-universe config (handler variant, payout %) |
| `ShopProduct` | Product catalog (price, creator email) |
| `ShopOrder` | Customer order (status: created → authorized → shipped → paid) |
| `ShopLedgerEntry` | Financial ledger (shop_revenue, creator_{email}) |
| `ShopCreator` | Creator tracking (balance, payout_status) |
| `ShopAppliedWebhookEvent` | Idempotency tracking (webhook event IDs already applied) |
| `ShopErrorLog` | Error tracking (PayPal errors with swallowed flag) |

**Migration**: `backend/migrations/versions/007_shop.py` creates all tables with proper foreign keys, indexes, and constraints.

### ✅ PayPal HTTP Client (backend/app/shop/paypal_client.py)

`PayPalClient`: An HTTP client that talks to the twin's PayPal-compatible API like a real shop integration would.

- OAuth token management with virtual time support
- Orders v2 API: create, get, approve, authorize, capture
- Payments v2 API: capture, refund, void, reauthorize
- Payouts v1 API: create batch, get batch/item, cancel item

**Key Design**: Does NOT import twin internals. Talks over HTTP using base_url from config.

### ✅ Buggy Handlers (backend/app/shop/handlers_buggy.py)

`BuggyShopHandlers`: Production-like code with six realistic, deliberate bugs:

| Bug ID | Issue | Trigger |
|--------|-------|---------|
| **B1** | Capture error swallowed, order marked paid | Auth expires (30d) → capture fails |
| **B2** | Token cached forever (wall time) | Advance clock 9+ hours |
| **B3** | Creators marked paid at submit, not SUCCESS | Payout submitted immediately |
| **B4** | Webhook handler not idempotent | Webhook retry → double-apply |
| **B5** | No signature verification | Chaos: tampered webhook |
| **B6** | Out-of-order webhook blindness | REFUNDED before COMPLETED |

Each bug is documented inline with clear comments showing where it is and why it's buggy.

### ✅ Reference Fix Handlers (backend/app/shop/handlers_reference_fix.py)

`ReferenceFixShopHandlers`: Identical interface to buggy handlers, all bugs fixed:

- B1: Propagate capture errors, don't mark shipped
- B3: Mark 'submitted' not 'paid', wait for SUCCESS
- B4: Check ShopAppliedWebhookEvent before applying
- And more...

Used as ground truth to verify invariants pass when bugs are fixed.

### ✅ Handler Loader (backend/app/shop/loader.py)

`get_shop_handlers()`: Loads the appropriate handler variant per universe based on `ShopSettings.handler_variant`:

```python
if settings.handler_variant == "buggy":
    return BuggyShopHandlers(...)
elif settings.handler_variant == "reference_fix":
    return ReferenceFixShopHandlers(...)
```

### ✅ Invariants Oracle (backend/app/scenarios/invariants.py)

`InvariantsOracle`: 7 independent invariants that verify correctness:

| Invariant | Checks |
|-----------|--------|
| **INV-1** | No shipped order without COMPLETED capture in twin |
| **INV-2** | Shop revenue ledger = twin captures - refunds |
| **INV-3** | Creator marked 'paid' only if payout SUCCESS |
| **INV-4** | Each webhook event applied at most once |
| **INV-5** | No tampered webhook was accepted |
| **INV-6** | Fully refunded order not in paid state |
| **INV-7** | No PayPal error was swallowed silently |

**Key Design**: Reads database directly, never calls shop handlers (oracle can't share bugs).

### ✅ Bug Documentation (BUGS.md)

Comprehensive registry describing each bug:
- Symptom: What goes wrong
- Location: File and function
- Trigger: How to make it appear
- Why Realistic: Why a developer would write this
- Fix: How to correct it
- Invariants Violated: Which checks fail

## How to Use

### 1. Happy Path (No Time Travel)

```python
# Place an order
shop_order_id, approval_link = await handlers.place_order(...)

# Customer approves at PayPal
await handlers.authorize_order_after_approval(shop_order_id, payer_id, universe_id)

# Ship the order
await handlers.ship_order(shop_order_id, universe_id)

# Check invariants - all pass!
oracle = InvariantsOracle(db, universe_id)
results = await oracle.check_all()
assert all(r.passed for r in results.values())
```

### 2. Trigger a Bug (With Time Travel)

```python
# Use buggy handlers
settings.handler_variant = "buggy"

# Place order and authorize
await handlers.place_order(...)
await handlers.authorize_order_after_approval(...)

# Advance virtual clock 31 days (auth expires after 29 days)
await control_api.clock.advance(by="31 days")

# Try to ship - capture fails silently (BUG!)
await handlers.ship_order(...)  # Marks paid anyway

# Check invariants - INV-1 fails!
results = await oracle.check_all()
assert not results["INV-1"].passed
```

### 3. Verify Fix Works

```python
# Use reference fix handlers
settings.handler_variant = "reference_fix"

# Same flow, but capture error is propagated
await handlers.authorize_order_after_approval(...)
await control_api.clock.advance(by="31 days")
await handlers.ship_order(...)  # Raises error, doesn't mark paid

# Check invariants - all pass!
results = await oracle.check_all()
assert all(r.passed for r in results.values())
```

## Test Structure

File: `backend/tests/test_shop_bugs.py`

Currently implemented:
- `test_happy_path_no_time_travel`: Invariants pass at time=0
- `test_bug_b1_capture_error_swallowed`: B1 trigger
- `test_bug_b3_payout_marked_paid_too_early`: B3 trigger
- `test_bug_b4_webhook_double_apply`: B4 trigger
- `test_invariants_oracle_structure`: Oracle interface

Ready for HTTP endpoint integration and full end-to-end testing.

## Architecture Principles

1. **No Twin Imports in Shop**: Shop code talks to twin over HTTP, never imports `app.twin.*`
2. **Dual Handler Variants**: Identical interface for easy swapping during tests
3. **Independent Oracle**: Invariants read database directly, can't share shop bugs
4. **Virtual Time Only**: All expiry/return calculations use virtual_now, not wall time
5. **Decimal Money**: No floats, all amounts are Decimal(19,8)
6. **Deterministic**: Results depend only on data and virtual time, not wall time or randomness

## What's Next

To complete Lumen Store integration:

1. **HTTP Endpoints**: Create `/shop/{universe_id}/...` routes
   - POST /shop/{universe_id}/seed (create products)
   - POST /shop/{universe_id}/orders (place order)
   - POST /shop/{universe_id}/orders/{id}/return (approval)
   - POST /shop/{universe_id}/orders/{id}/ship (ship order)
   - POST /shop/{universe_id}/webhooks/paypal (webhook receiver)
   - GET /shop/{universe_id}/ledger (view ledger)
   - GET /shop/{universe_id}/state (view state)

2. **Webhook Receiver**: Implement signature verification (B5) and out-of-order handling (B6)

3. **End-to-End Tests**: Full shop flow with time travel and invariant checks

4. **Manual Testing**: Use Lumen Store UI to:
   - Place orders
   - Approve in PayPal
   - Advance clock and watch bugs appear
   - Verify invariants fail/pass

## Files

| File | Purpose | Status |
|------|---------|--------|
| `app/models.py` | Shop models (7 new) | ✅ Done |
| `migrations/versions/007_shop.py` | Schema migration | ✅ Done |
| `app/shop/paypal_client.py` | HTTP client | ✅ Done |
| `app/shop/handlers_buggy.py` | Buggy handlers | ✅ Done |
| `app/shop/handlers_reference_fix.py` | Fixed handlers | ✅ Done |
| `app/shop/loader.py` | Handler variant loader | ✅ Done |
| `app/scenarios/invariants.py` | Invariants oracle | ✅ Done |
| `BUGS.md` | Bug documentation | ✅ Done |
| `tests/test_shop_bugs.py` | Test structure | ✅ Done |
| `app/twin/routers/shop.py` | Shop HTTP endpoints | ⏳ Pending |

## Verification

```bash
# All components compile and import
python -c "from app.shop import *; from app.scenarios.invariants import InvariantsOracle"

# Database migration succeeds
cd backend && alembic upgrade head

# Code quality checks pass
uv run ruff check app/shop app/scenarios/invariants.py
```

## Key Insights

1. **Time-Based Bugs Are Real**: Many integration bugs only appear when time moves (token expiry, async settlement, retry logic). The twin's virtual clock makes them testable and reproducible.

2. **Determinism Matters**: By using virtual_now everywhere, bugs are deterministic and predictable. A developer can reproduce exactly the conditions that triggered a bug.

3. **Invariants Enable Verification**: Rather than checking specific behavior, invariants check the **final state** against ground truth. This is more robust to implementation changes.

4. **Oracle Independence**: The invariants oracle reads the database directly, never calling shop code. This prevents the oracle from inheriting bugs in the shop implementation.

5. **Dual Handler Design**: Having buggy and reference_fix handlers with identical interfaces makes testing trivial: same test, different handler, different result.

## Demo Workflow

1. Developer uses Lumen Store UI to place an order
2. Approves payment in PayPal (simulated)
3. Ships order immediately - everything works (bugs are latent)
4. Runs invariants - all pass
5. Uses `/chronos/api` to advance virtual clock 31 days
6. Ships another order - capture fails silently (BUG!)
7. Runs invariants - INV-1 fails (no COMPLETED capture)
8. Switches to reference_fix handlers
9. Runs same test - now it works correctly
10. Agent analyzes the code and explains the bug

This demonstrates how the twin + Lumen Store can teach developers about real PayPal integration patterns.

