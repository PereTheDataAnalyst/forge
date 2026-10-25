"""MCP access groups. Pure logic: no database, Redis, web framework or MCP SDK needed."""

from forge_integrations.mcp.scopes import ALL_SCOPES

from app.modules.mcp import catalog

EXPECTED_TOOLS = {
    "get_model_info",
    "get_project_info",
    "get_usage",
    "generate",
    "chat",
    "translate",
    "transcribe",
    "evaluate",
}


def test_every_library_scope_is_one_group_and_no_other_scope_exists():
    scopes = [group.scope for group in catalog.ACCESS_GROUPS]
    assert len(scopes) == len(set(scopes))
    assert set(scopes) == set(ALL_SCOPES)


def test_the_tools_are_exactly_the_eight_documented_ones_and_none_is_administrative():
    assert set(catalog.ALL_TOOLS) == EXPECTED_TOOLS
    assert len(catalog.ALL_TOOLS) == len(set(catalog.ALL_TOOLS))
    for tool in catalog.ALL_TOOLS:
        for forbidden in ("admin", "create_key", "revoke", "delete", "update_project", "settings"):
            assert forbidden not in tool


def test_the_default_access_is_read_only_and_known():
    by_scope = {group.scope: group for group in catalog.ACCESS_GROUPS}
    assert catalog.DEFAULT_ACCESS
    for scope in catalog.DEFAULT_ACCESS:
        assert by_scope[scope].read_only


def test_read_only_groups_are_the_ones_that_do_not_generate_or_run_anything():
    read_only = {g.scope for g in catalog.ACCESS_GROUPS if g.read_only}
    assert read_only == {"models:read", "project:read", "usage:read"}


def test_normalise_access_orders_by_catalogue_and_removes_duplicates():
    assert catalog.normalise_access(["generate", "models:read", "generate"]) == [
        "models:read",
        "generate",
    ]
    assert catalog.normalise_access([" usage:read "]) == ["usage:read"]


def test_normalise_access_names_every_unknown_value_and_refuses_an_empty_choice():
    for bad in (["admin"], ["models:read", "root", "sudo"], []):
        try:
            catalog.normalise_access(bad)
        except ValueError as exc:
            if bad:
                for item in set(bad) - {"models:read"}:
                    assert item in str(exc)
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_tools_for_lists_whole_groups_in_catalogue_order():
    assert catalog.tools_for(["generate"]) == ["generate", "chat", "translate"]
    assert catalog.tools_for(["transcribe", "models:read"]) == ["get_model_info", "transcribe"]
    assert catalog.tools_for([]) == []


def test_scopes_are_read_back_from_the_tool_list_and_round_trip():
    for group in catalog.ACCESS_GROUPS:
        tools = catalog.tools_for([group.scope])
        assert catalog.scopes_for(tools) == frozenset({group.scope})
    everything = catalog.tools_for([g.scope for g in catalog.ACCESS_GROUPS])
    assert catalog.scopes_for(everything) == frozenset(ALL_SCOPES)


def test_a_partly_listed_group_grants_nothing():
    # `chat` and `translate` are not enough to unlock the generate group.
    assert catalog.scopes_for(["generate"]) == frozenset()
    assert catalog.scopes_for(["chat", "translate", "get_model_info"]) == frozenset({"models:read"})


def test_unknown_tool_names_grant_nothing():
    assert catalog.scopes_for(["rm_rf", "admin"]) == frozenset()
    assert catalog.access_for(["rm_rf"]) == []


def test_limits_and_the_invoke_scope():
    assert catalog.MCP_PATH == "/mcp"
    assert catalog.INVOKE_SCOPE == "mcp.invoke"
    assert catalog.DEFAULT_EXPIRY_DAYS in catalog.EXPIRY_CHOICES_DAYS
    assert max(catalog.EXPIRY_CHOICES_DAYS) == 90  # the library's maximum token lifetime
    assert catalog.EXPIRY_CHOICES_DAYS == (30, 90)
    assert 1 <= catalog.MAX_CLIENTS_PER_PROJECT <= 20
