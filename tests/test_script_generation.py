from freecad_llm_agent.script_generation import ScriptGenerationContext, ScriptGenerator


class RecordingLLM:
    def __init__(self) -> None:
        self.messages = []

    def complete(self, messages, images=None):  # type: ignore[no-untyped-def]
        self.messages = list(messages)
        return "print('ok')"


def test_prompt_includes_history_and_document_rules():
    llm = RecordingLLM()
    generator = ScriptGenerator(llm)
    context = ScriptGenerationContext(
        requirement="Создать деталь",
        previous_errors=["RuntimeError: boom"],
        request_additional_views=True,
        script_history=["print('first')", "print('second')"],
    )
    script = generator.generate(context)

    assert script == "print('ok')"
    assert llm.messages, "LLM must receive at least one message"
    prompt = llm.messages[-1].content
    assert "=== PREVIOUS PYTHON CONTEXT ===" in prompt
    assert "print('second')" in prompt
    assert "LLMAgentProject" in prompt
    assert "Return only Python code" in prompt
