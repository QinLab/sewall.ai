# Model configurations

Each file routes one role (planner or reviewer) to a Vertex AI model. Copy an example, set
`project` to your own Google Cloud project, and keep credentials out of the file: the client
uses Application Default Credentials only. `auth_method` must be `adc`; API keys are rejected.
Fields are validated by `sewall.llm.ModelConfig`.
