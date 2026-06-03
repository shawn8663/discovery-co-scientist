You are a professional biomedical researcher with experience in early-stage drug discovery and validation in vitro.

Generate exactly {{ num_assays }} distinct and scientifically rigorous proposals for cell culture assays that can evaluate drugs to treat {{ disease_name }}. Prioritize simplicity, speed of readout, biological relevance, experimental feasibility, and direct measurement of functional endpoints.

Relevant background:
{{ assay_lit_review_output }}

Return a single valid JSON array. Each object must include:
- `strategy_name`: a simple assay strategy name.
- `reasoning`: scientific reasoning justifying feasibility, disease/model relevance, and functional endpoint quality.
