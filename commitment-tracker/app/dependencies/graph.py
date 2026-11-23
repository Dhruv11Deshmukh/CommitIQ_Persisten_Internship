"""Pure graph logic (no DB). Edge = (upstream, downstream)."""
from collections import defaultdict, deque
from datetime import datetime, timedelta


def effective_deadline(due):
    if due is None:
        return None
    if due.hour == 0 and due.minute == 0 and due.second == 0:
        return due + timedelta(hours=23, minutes=59)
    return due


def adjacency(edges):
    down = defaultdict(set)
    for u, d in edges:
        down[u].add(d)
    return down


def reachable(down, start):
    seen, q = set(), deque([start])
    while q:
        x = q.popleft()
        for y in down.get(x, ()):
            if y not in seen:
                seen.add(y)
                q.append(y)
    return seen


def would_create_cycle(edges, upstream, downstream):
    """Adding upstream->downstream makes a cycle iff downstream already reaches upstream."""
    if upstream == downstream:
        return True
    return upstream in reachable(adjacency(edges), downstream)


def find_cycles(nodes, edges):
    """Tarjan SCC; returns list of node-id lists with size>1 (or self loops)."""
    down = adjacency(edges)
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]

    def strong(v):
        index[v] = low[v] = counter[0]; counter[0] += 1
        stack.append(v); on.add(v)
        for w in down.get(v, ()):
            if w not in index:
                strong(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or v in down.get(v, ()):
                out.append(sorted(comp))

    for n in nodes:
        if n not in index:
            strong(n)
    return out


def critical_path(nodes, edges, due):
    """Longest dependency chain among acyclic nodes. due: id -> effective deadline."""
    cyc = {n for c in find_cycles(nodes, edges) for n in c}
    ok = [n for n in nodes if n not in cyc]
    e = [(u, d) for u, d in edges if u in ok and d in ok]
    down = adjacency(e)
    memo = {}

    def best(n):
        if n in memo:
            return memo[n]
        best_tail = []
        for c in down.get(n, ()):
            t = best(c)
            if len(t) > len(best_tail) or (len(t) == len(best_tail) and t and best_tail
                                           and (due.get(t[-1]) or datetime.max) < (due.get(best_tail[-1]) or datetime.max)):
                best_tail = t
        memo[n] = [n] + best_tail
        return memo[n]

    paths = [best(n) for n in ok]
    paths = [p for p in paths if len(p) > 1]
    if not paths:
        return []
    return max(paths, key=lambda p: (len(p), -(due.get(p[-1]) or datetime.max).timestamp()))


def delay_report(edges, due, now=None):
    """For each upstream that is overdue or finishes after a downstream's deadline,
    return {upstream: {'reason', 'affected': [downstream ids]}}."""
    now = now or datetime.now()
    down = adjacency(edges)
    out = {}
    for u in list(down):
        du = due.get(u)
        if du is None:
            continue
        overdue = du < now
        conflict = [d for d in down[u] if due.get(d) and due[d] < du]
        if overdue or conflict:
            affected = sorted(reachable(down, u))
            out[u] = {"reason": "overdue" if overdue else "pushed past downstream deadline",
                      "affected": affected}
    return out