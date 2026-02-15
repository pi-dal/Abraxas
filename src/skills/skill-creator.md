When asked to create or update a skill, use this workflow:

1. Clarify the skill goal, trigger conditions, and output contract.
2. Keep instructions concise and implementation-focused.
3. Follow a skills-first strategy. If skills alone cannot solve it, use plugin extension points in `src/plugins`.
4. Require safe failure behavior and readable error messages.
5. Include concrete examples of expected usage and boundaries.

Skill authoring rules:
- A skill should solve one clear problem.
- Avoid vague policy text; provide actionable steps.
- Keep compatibility with existing core runtime constraints.
