from interpreter import interpreter

interpreter.offline = True
#interpreter.llm.temperature = 0.7
#interpreter.llm.context_window = 16000
#interpreter.llm.max_tokens = 100
#interpreter.llm.max_output = 1000
interpreter.llm.model = "ollama/gemma3:12b"
interpreter.llm.api_base = "http://localhost:11434"
interpreter.profile = "config/oi_profile.yaml"

interpreter.chat("デスクトップにはいくつファイルがある?")