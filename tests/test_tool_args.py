from src.minibot.core.tool_args import parse_tool_arguments


def test_parse_tool_arguments_valid_object():
    parsed, note = parse_tool_arguments('{"team_name":"ResearchTeam","member_count":2}')
    assert parsed == {"team_name": "ResearchTeam", "member_count": 2}
    assert note is None


def test_parse_tool_arguments_with_trailing_json_recovers_first_object():
    raw = '{"team_name":"ResearchTeam","member_count":2}{"extra":true}'
    parsed, note = parse_tool_arguments(raw)
    assert parsed == {"team_name": "ResearchTeam", "member_count": 2}
    assert note == "Trailing content after JSON object was ignored"


def test_parse_tool_arguments_invalid_returns_error():
    parsed, note = parse_tool_arguments('{"team_name":')
    assert parsed is None
    assert note is not None


def test_parse_tool_arguments_fenced_json():
    raw = """```json
{"action":"list"}
```"""
    parsed, note = parse_tool_arguments(raw)
    assert parsed == {"action": "list"}
    assert note is None
