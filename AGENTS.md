# Discovery 3.0 repository instructions

## Engineering completion reports

For every future implementation task, provide a substantive engineering completion
report directly in the final response. Apply this standard to all development work,
bug fixes, testing, and architectural changes.

The report must include:

1. **Implementation summary:** Explain what changed, why it changed, and how the
   application behaves differently.
2. **Root causes:** Explain every significant defect discovered, including the
   affected components and failure mechanism. If no defect was discovered, say so.
3. **Architecture:** Describe important design decisions, persistence changes,
   security implications, and tradeoffs.
4. **Testing:** Report the exact test suites executed, their results, and which
   previously established suites were not rerun.
5. **Data integrity:** Identify migrations, database changes, data preservation
   checks, and potential compatibility concerns. Explicitly state when none apply.
6. **Outstanding issues:** Clearly distinguish verified functionality, simulated
   behavior, untested functionality, and known limitations.
7. **Deliverables:** Provide links to relevant reports, screenshots, and changed
   documentation. Identify deliverables that do not apply to the task.
8. **Next steps:** Recommend the next engineering milestone and explain why.

Do not substitute a three-sentence summary and documentation links for an engineering
report. Do not claim a feature is verified merely because its tests passed with
mocks. Keep the report concise enough to read efficiently, but detailed enough that
another senior engineer can understand what happened without opening the repository.

Preserve existing repository instructions when adding or updating this standard.
