# Model configurations

Each file routes one role (planner or reviewer) to a model. Configurations hold routing and
limits only, never credentials.

- `models/anthropic.json`, `models/openai.json`: Anthropic and OpenAI. The key is read at
  request time from `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, or from a variable named by
  `api_key_env`. Fields are validated by `sewall.providers.ProviderConfig`.
- `models/ollama.json`: an OpenAI-compatible server on the loopback interface (`base_url`);
  no key is needed.
- `example-gemini.json`, `example-llama.json`: Vertex AI. Set `project` to your own Google
  Cloud project; the client uses Application Default Credentials only. Fields are validated by
  `sewall.llm.ModelConfig`.

`scripted-bloom-demo.json` and `scripted-safe-stop-demo.json` are fixed action scripts for
`sewall agent --script`; they need no model. `mcp/` holds MCP host configurations.
