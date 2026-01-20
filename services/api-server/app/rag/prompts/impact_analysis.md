You are analyzing the impact of code changes.

## Retrieved Context
{context}

{parent_context}

## Question
{query}

## Instructions
Analyze dependencies and potential impact:

1. **Direct Dependencies** - What directly uses this code?
2. **Transitive Impact** - What else could be affected downstream?
3. **Risk Assessment** - High/Medium/Low impact areas
4. **Testing Recommendations** - What should be tested after changes?

Guidelines:
- Reference specific files and modules
- Be concrete: "UserService depends on this" not "some services"
- Consider both code dependencies and behavioral contracts
- If you can't determine impact from context, say so

## Answer
