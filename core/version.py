def _parse(v):
    parts = []
    for x in str(v).split("."):
        try:
            parts.append(int(x))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _cmp(a, b):
    pa, pb = _parse(a), _parse(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def satisfies(version, constraint):
    if not constraint or constraint == "*":
        return True
    for clause in str(constraint).split(","):
        clause = clause.strip()
        if not clause:
            continue
        op = "="
        for candidate in (">=", "<=", "!=", "==", ">", "<", "="):
            if clause.startswith(candidate):
                op = candidate
                clause = clause[len(candidate):].strip()
                break
        c = _cmp(version, clause)
        if op == ">=" and c < 0:
            return False
        if op == ">" and c <= 0:
            return False
        if op == "<=" and c > 0:
            return False
        if op == "<" and c >= 0:
            return False
        if op in ("=", "==") and c != 0:
            return False
        if op == "!=" and c == 0:
            return False
    return True