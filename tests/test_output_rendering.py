from src.minibot.utils import output


def test_print_tool_output_skips_empty(monkeypatch, capsys):
    monkeypatch.setattr(output, "rich_enabled", lambda: False)

    output.print_tool_output("Bash", "")
    output.print_tool_output("Bash", "   ")

    captured = capsys.readouterr()
    assert captured.out == ""
