"""
Dependency-graph traversal for reversal-cost calculation.

Instead of hand-writing "if action_type == X, check tables Y and Z",
we ask the database itself which tables point at which (foreign keys),
then walk that graph outward from the record being touched.

This is deterministic code, not a trained model — same input always
gives the same output, so it stays replayable in the Evidence Ledger.
"""
from sqlalchemy import inspect, text


# ---------------------------------------------------------------------------
# Step 1: pull the foreign-key graph straight out of the database schema.
# Nothing here is hardcoded per table — add a new table with a new FK and
# it shows up automatically next time this runs.
# ---------------------------------------------------------------------------
def get_dependency_graph(engine):
    inspector = inspect(engine)
    graph = {}
    for table in inspector.get_table_names():
        graph[table] = []
        for fk in inspector.get_foreign_keys(table):
            graph[table].append({
                "child_table": table,
                "child_columns": fk["constrained_columns"],
                "parent_table": fk["referred_table"],
                "parent_columns": fk["referred_columns"],
            })
    return graph


# ---------------------------------------------------------------------------
# Step 2: the one piece that still needs a human judgment call — does
# restoring the PARENT record automatically heal this CHILD row, or did the
# child already cause a real-world side effect that a restore can't undo?
#
# This is set once per relationship (table edge), not per action type —
# a handful of lines cover the whole schema, versus hundreds of
# per-action rules in the old approach.
# ---------------------------------------------------------------------------
EDGE_HEALS_ON_RESTORE = {
    ("orders", "users"): True,           # order just references the user, restore fixes it
    ("payments", "orders"): False,       # money already charged — restore won't undo that
    ("reviews", "products"): True,       # review just references the product
    ("subscriptions", "users"): True,
    ("invoices", "subscriptions"): False,  # invoice already sent to the customer
}


# ---------------------------------------------------------------------------
# Step 3: BFS outward from the record being touched, following every FK
# edge that points AT this table. One function, works for any table.
# ---------------------------------------------------------------------------
def find_dependents(engine, graph, table, record_id, _visited=None, _depth=0):
    if _visited is None:
        _visited = set()

    found = []
    with engine.connect() as conn:
        for child_table, fks in graph.items():
            for fk in fks:
                if fk["parent_table"] != table:
                    continue
                col = fk["child_columns"][0]
                rows = conn.execute(
                    text(f"SELECT id FROM {child_table} WHERE {col} = :rid"),
                    {"rid": record_id},
                ).fetchall()

                for (child_id,) in rows:
                    key = (child_table, child_id)
                    if key in _visited:
                        continue
                    _visited.add(key)

                    heals = EDGE_HEALS_ON_RESTORE.get((child_table, table), False)
                    found.append({
                        "table": child_table,
                        "id": child_id,
                        "depth": _depth + 1,
                        "heals_on_restore": heals,
                    })

                    # keep walking outward from this dependent
                    found += find_dependents(
                        engine, graph, child_table, child_id, _visited, _depth + 1
                    )
    return found


# ---------------------------------------------------------------------------
# Step 4: reversal cost as a normalized 0-10 SCORE, not rupees.
#
# Why a score and not currency: the Weighted Risk Engine combines this
# with policy/RAG compliance and intent & impact into one weighted sum
# (score = wA*reversal + wB*policy + wC*intent_impact). You can't add
# rupees to a 0-10 policy score — everything feeding that sum has to
# live on the same scale. The dependency-graph TRAVERSAL is unchanged;
# only the number it produces at the end is different.
#
#   0  = trivially reversible   (snapshot restores it, nothing unhealed)
#   10 = effectively irreversible (no undo path, and/or many real-world
#        side effects a restore can't touch)
#
# Two components, both bounded, so the total always saturates at 10
# instead of growing without limit as a rupee figure would:
#   - restore_difficulty : 0 if an undo/snapshot exists, else 4 — a
#                           flat penalty for "there is no fast undo"
#   - side_effect_load    : scales with how many dependents won't heal
#                           on restore, capped at 6 so it saturates
#                           rather than scaling forever with fan-out
# ---------------------------------------------------------------------------
def reversal_cost_score(engine, graph, table, record_id,
                         snapshot_available, max_unhealed_for_scale=6,
                         truly_irreversible=False):
    if truly_irreversible:
        return {"score": 10.0, "unbounded": True, "dependents": []}

    dependents = find_dependents(engine, graph, table, record_id)
    unhealed = [d for d in dependents if not d["heals_on_restore"]]

    restore_difficulty = 0.0 if snapshot_available else 4.0
    side_effect_load = min(6.0, (len(unhealed) / max_unhealed_for_scale) * 6.0)
    score = round(min(10.0, restore_difficulty + side_effect_load), 1)

    return {
        "score": score,
        "unbounded": False,
        "restore_difficulty": restore_difficulty,
        "side_effect_load": round(side_effect_load, 1),
        "dependents_found": len(dependents),
        "dependents_unhealed": len(unhealed),
        "dependents": dependents,
    }
