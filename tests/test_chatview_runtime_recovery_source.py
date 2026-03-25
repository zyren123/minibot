from pathlib import Path


def test_chatview_uses_answer_stream_resume_and_keeps_interrupted_content() -> None:
    source = Path("webui/src/views/ChatView.tsx").read_text(encoding="utf-8")

    assert "submitPendingQuestionAnswerStream(" in source
    assert '"Generation interrupted."' not in source


def test_chatview_reads_usage_metadata_from_ask_user_question_events() -> None:
    source = Path("webui/src/views/ChatView.tsx").read_text(encoding="utf-8")

    ask_branch = source.split('if (ev.type === "ask_user_question") {', 1)[1].split('if (ev.type === "ask_user_answer_received") {', 1)[0]

    assert "normalizeUsage(ev.usage)" in ask_branch
    assert "normalizeUsage(ev.context_usage)" in ask_branch
