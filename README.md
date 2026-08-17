# OCR INVOICE PROCESSING

*Assumptions, edge cases, and use of AI*

## Assumptions

I treated OCR output as untrusted and assumed USD amounts. Negatives are valid, while values outside -$10 million to $10 million are invalid. Year-first dates use year-month-day; year-last dates use US month-day-year. Future dates and dates more than 100 years old are flagged. Invoice IDs are unique, so every duplicate occurrence is flagged. Valid unchanged records go only to the cleaned output; safely corrected records go to both outputs with a warning; invalid records or those missing required values go only to the flagged output with an error reason.

## Edge cases

There are more cases that fit here. Here are some examples: Corrected O to 0 only when it produced a valid amount, accepted proper comma grouping and removable internal spaces, and treated N/A or blanks as missing. Corrections are flagged. For duplicate invoice IDs, I flag every occurrence because the original cannot be identified reliably.

## How I used AI

I spent most of the task iterating on a detailed specification. AI helped refine planning questions, validation rules, data models, and flow charts, and then assisted with implementation and testing. The first implementation was mostly correct because the specification was detailed, but it overcomplicated some areas and omitted some docstrings, which I asked AI to add. During review, I reconsidered my initial rule that only a later invalid duplicate should be flagged. Because duplicate IDs make the original uncertain, I changed the rule to flag every occurrence and added a test for it. I reviewed and revised the result rather than accepting the first draft.

## Links

- https://chatgpt.com/share/6a827e15-e2b0-83eb-954e-d149bb502b4f
- https://chatgpt.com/share/6a82910d-26c4-83eb-8683-a09ea8be7dd0
- https://chatgpt.com/share/6a82911c-5950-83eb-838b-eea634b37f12
- https://chatgpt.com/share/6a829179-f880-83ed-ad58-b4cbf14d6fae

Note: I have a local conversation stored in [local_ai_logs/implement.jsonl](local_ai_logs/implement.jsonl) but it's very hard to read
      for a human but exported as required.
      It is a codex session for generating code locally. Can't share via link.
