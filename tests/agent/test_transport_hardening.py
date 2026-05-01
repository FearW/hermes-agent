"""Regression tests for VPS hard failures in transport registration."""


def test_chat_completions_transport_registers_with_helper_modules():
    from agent.transports import get_transport

    transport = get_transport("chat_completions")

    assert transport is not None
    assert hasattr(transport, "build_kwargs")


def test_failover_reason_members_used_by_run_agent_exist():
    from agent.error_classifier import FailoverReason


    assert FailoverReason.image_too_large.value == "image_too_large"
    assert FailoverReason.oauth_long_context_beta_forbidden.value == "oauth_long_context_beta_forbidden"
