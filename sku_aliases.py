"""The SKU the platform stored is not always the SKU we sent.

Some accounts carry listings the platform holds under a corrupted spelling of our SKU (a prepended zero, a rounded
number); every by-SKU write to them is then refused as "SKU does not exist". This module translates at the
boundary - outbound writes go to the SKU the platform holds, answers come back under the SKU we hold - and the
rest of the pipeline keeps working in true SKUs.

THIS ACCOUNT has no alias data file (sku_aliases.csv), so the module is a deliberate no-op: every SKU goes out
exactly as it is. It exists because delete_listings_batch.py imports it (the same tool runs on every store); if a
corrupted listing ever appears, probe_sku_rounding.py regenerates the data file and the bridge starts working.
Same module as the stores where aliases were needed. A pair is only usable when it is unambiguous in BOTH
directions; anything that collides gets no alias at all.
"""
import csv
import io
import os
from collections import Counter

ALIAS_FILE = os.getenv("SKU_ALIAS_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sku_aliases.csv")


def load_aliases(path=None):
    """(true -> stored, stored -> true). Ambiguous pairs on either side are
    dropped rather than guessed - see the module docstring."""
    path = path or ALIAS_FILE
    pairs = []
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                true_sku = str(row.get("true_sku") or "").strip()
                onbuy_sku = str(row.get("onbuy_sku") or "").strip()
                if true_sku and onbuy_sku and true_sku != onbuy_sku:
                    pairs.append((true_sku, onbuy_sku))
    except FileNotFoundError:
        return {}, {}
    lhs = Counter(t for t, _ in pairs)
    rhs = Counter(o for _, o in pairs)
    forward = {t: o for t, o in pairs if lhs[t] == 1 and rhs[o] == 1}
    return forward, {o: t for t, o in forward.items()}


TO_ONBUY, TO_TRUE = load_aliases()


def to_onbuy(sku):
    """The SKU to put on the wire for this row's listing."""
    s = str(sku or "").strip()
    return TO_ONBUY.get(s, s)


def to_true(sku):
    """The SKU the sheet knows, given whatever the API just returned."""
    s = str(sku or "").strip()
    return TO_TRUE.get(s, s)


def describe():
    return f"{len(TO_ONBUY)} SKU alias(es) loaded from {os.path.basename(ALIAS_FILE)}"
