from hermes_cli.capabilities import build_capabilities_dashboard


def test_capabilities_dashboard_smoke():
    text = build_capabilities_dashboard()
    assert "# Hermes Capabilities Dashboard" in text
    assert "## L4 Memory" in text
    assert "## Workflows" in text
    assert "## Skills" in text
