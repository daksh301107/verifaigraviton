"""
Run this to see the whole thing work end to end:

    python3 demo.py

It builds a small seeded SQLite DB, then asks "what happens if we delete
product #1?" — the graph traversal discovers every dependent automatically
(reviews directly, orders/payments/invoices through the user — simulating
a cross-cutting delete), classifies which ones a restore would heal, and
feeds the count straight into the reversal-cost SCORE (0-10) used by the
Weighted Risk Engine — not rupees, so it combines cleanly with the other
0-10 factors (policy/RAG, intent & impact).
"""
from schema import build_demo_db
from depgraph import get_dependency_graph, find_dependents, reversal_cost_score


def line():
    print("-" * 64)


if __name__ == "__main__":
    engine = build_demo_db("demo.db")
    graph = get_dependency_graph(engine)

    print("1. Dependency graph auto-discovered from the DB schema")
    line()
    for table, fks in graph.items():
        for fk in fks:
            print(f"  {fk['child_table']:<14} --> {fk['parent_table']}")
    print()

    print("2. Case A — delete Product #1 (direct dependents only: reviews)")
    line()
    result_a = reversal_cost_score(
        engine, graph, table="products", record_id=1,
        snapshot_available=True,
    )
    for d in result_a["dependents"]:
        heal = "heals on restore" if d["heals_on_restore"] else "does NOT heal"
        print(f"  found: {d['table']} #{d['id']}  (depth {d['depth']}, {heal})")
    print(f"  dependents found total : {result_a['dependents_found']}")
    print(f"  dependents unhealed    : {result_a['dependents_unhealed']}")
    print(f"  restore difficulty     : {result_a['restore_difficulty']} / 10")
    print(f"  side-effect load       : {result_a['side_effect_load']} / 10")
    print(f"  REVERSAL COST SCORE    : {result_a['score']} / 10")
    print()

    print("3. Case B — delete User #1 (cascades through orders, payments,")
    print("   subscriptions, invoices — multi-hop, no snapshot available)")
    line()
    result_b = reversal_cost_score(
        engine, graph, table="users", record_id=1,
        snapshot_available=False,
    )
    for d in sorted(result_b["dependents"], key=lambda x: x["depth"]):
        heal = "heals on restore" if d["heals_on_restore"] else "does NOT heal"
        print(f"  found: {d['table']} #{d['id']}  (depth {d['depth']}, {heal})")
    print(f"  dependents found total : {result_b['dependents_found']}")
    print(f"  dependents unhealed    : {result_b['dependents_unhealed']}")
    print(f"  restore difficulty     : {result_b['restore_difficulty']} / 10")
    print(f"  side-effect load       : {result_b['side_effect_load']} / 10")
    print(f"  REVERSAL COST SCORE    : {result_b['score']} / 10")
    print()

    print("Note: not one line of this traversal logic mentions 'product',")
    print("'user', 'order' or any specific action type by name. Add a new")
    print("table with a foreign key tomorrow and it is picked up for free.")
    print("The score, not a rupee figure, is what the Weighted Risk Engine")
    print("consumes downstream.")
