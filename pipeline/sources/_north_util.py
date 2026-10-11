"""Helpers for the north group (shanxi / beijing / heilongjiang / jilin / mengdong)."""
from collections import Counter


def consensus_ocr_only(parsed, anns, fields, period_key, is_ocr):
    """Regulated prices (输配/需量/容量) are constant within a TDP period: a value read by OCR that disagrees with the
    strict majority of the period's months (text-layer months included in the vote) is replaced.  Text-layer months
    are never modified.  Logged into ann['ocr_corrections']."""
    groups = {}
    for m, rows in parsed.items():
        for r in rows:
            for f in fields:
                if r.get(f) is not None:
                    groups.setdefault((period_key(m), r["category"], r["tariff_type"], r["voltage"], f), []).append((m, r))
    log = []
    for (pk, cat, tt, v, f), items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        cnt = Counter(r[f] for _, r in items)
        val, n = cnt.most_common(1)[0]
        if n < 2 or n <= len(items) / 2:
            continue
        for m, r in items:
            if r[f] != val and is_ocr(anns[m]):
                msg = f"{tt} {v} {f}: OCR {r[f]} -> {val} (regulated price, {n} of {len(items)} months of period {pk} agree)"
                anns[m].setdefault("ocr_corrections", []).append(msg)
                log.append(f"{m} {msg}")
                r[f] = val
    return log
