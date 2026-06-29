# Error Codes

The V3 error dictionary is defined in `clipforge_v3/error_codes.py`.

Groups:

- Product: `P001` through `P009`
- Mechanical installation: `M001` through `M008`
- Action: `A001` through `A008`
- Continuity: `C001` through `C008`
- Prompt: `R001` through `R006`

Each code includes Chinese explanation, English explanation, severity, likely cause, recommended handling, post-fix eligibility, and publish blocking flag.

Operators should use error codes during Take Review so Retake Planner can choose KEEP, FIX_IN_POST, EDIT, REROLL, or REWRITE.
