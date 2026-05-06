from plugins.memory.hindsight.local_graph import LocalRecallGraph


def test_local_recall_graph_community_routing_and_ppr(tmp_path):
    db = tmp_path / "g.sqlite"
    g = LocalRecallGraph(
        db_path=db,
        min_edge_weight=1.0,
        community_rebuild_every_ingests=1,
    )
    try:
        # Ingest two distinct co-occurrence clusters.
        g.ingest_recall_lines(
            [
                "Fix ImportError: libGL.so.1 by installing libgl1 (apt install libgl1)",
                "On Debian/Ubuntu, apt-get install libgl1-mesa-glx",
                "If you see libEGL errors, install mesa drivers",
            ]
        )
        g.ingest_recall_lines(
            [
                "Conda: create env with python=3.11 and activate it",
                "pip install -U pip setuptools wheel",
                "Use uv pip for faster installs",
            ]
        )

        candidates = [
            "Conda: create env with python=3.11 and activate it",
            "Fix ImportError: libGL.so.1 by installing libgl1 (apt install libgl1)",
        ]
        ranked = g.rank_lines_ppr(
            query="ImportError libGL.so.1 怎么解决",
            candidate_lines=candidates,
            max_return=2,
            community_top_k=3,
            ppr_steps=12,
        )
        assert ranked[0].lower().find("libgl") >= 0
    finally:
        g.close()

