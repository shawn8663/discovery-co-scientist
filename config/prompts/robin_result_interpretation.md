You are interpreting experimental analysis outputs to inform the next therapeutic discovery round for {{ disease_name }}.

Analysis summary:
{{ analysis_summary }}

Return structured JSON with:
- `summary`: concise interpretation of what the experiment showed.
- `positive_hits`: candidates or mechanisms with supportive evidence.
- `negative_hits`: candidates or mechanisms that failed or underperformed.
- `suggested_mechanisms`: plausible mechanisms supported by the data.
- `follow_up_assays`: targeted follow-up assays or validation experiments as outlines, not precise protocols.
- `constraints`: toxicity, delivery, model-system, or feasibility constraints to inject into future candidate generation.
