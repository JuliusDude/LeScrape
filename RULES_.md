# RULES.md — Agent Allocation and Delegation

## 1. Purpose

This document defines how Claude Opus, Sonnet, and Haiku should divide work when multiple models or subagents are available.

**Follow the task and requirements provided by the user.** This file governs only agent allocation, delegation, coordination, and review. It does not define or change the user's task, scope, priorities, architecture, constraints, or deliverables.

If anything in this file conflicts with an explicit user instruction, follow the user's instruction.

## 2. Roles and responsibilities

### Opus — Lead and accountable owner

Opus is responsible for the overall result.

Opus must:

- Understand the user's request and identify the required outcome.
- Break the work into appropriately sized, independently verifiable tasks.
- Decide which tasks to handle directly and which to delegate.
- Assign tasks to Sonnet or Haiku according to their complexity and risk.
- Provide each agent with sufficient context, precise instructions, and clear acceptance criteria.
- Coordinate dependencies and prevent conflicting work.
- Review delegated outputs before relying on or integrating them.
- Resolve disagreements and make final decisions.
- Verify the completed result against the user's requirements.
- Communicate uncertainties, limitations, and incomplete work honestly.

Opus remains accountable for all delegated work. Delegation does not transfer responsibility for correctness.

Opus should personally handle work that is ambiguous, high-impact, cross-cutting, sensitive, or requires significant judgment.

### Sonnet — Primary execution agent

Use Sonnet for substantial tasks with reasonably clear requirements.

Examples include:

- Multi-step implementation.
- Detailed technical or analytical investigation.
- Work spanning several related files or components.
- Building or revising substantial deliverables.
- Debugging that requires tracing multiple parts of a system.
- Writing and running focused tests.
- Producing structured findings for Opus to review.

Sonnet must:

- Stay within the assigned scope.
- Follow the user's requirements and Opus's task brief.
- Avoid unrelated refactoring or scope expansion.
- Report files or artifacts changed.
- Report tests or checks performed and their results.
- Identify assumptions, uncertainties, and unresolved issues.

Sonnet may recommend improvements, but must not silently change the user's requirements or make unapproved high-impact decisions.

### Haiku — Supporting execution agent

Use Haiku for small, bounded, low-risk tasks.

Examples include:

- Finding specific symbols, references, or text.
- Enumerating files, fields, functions, or data structures.
- Summarizing a specific source or output.
- Performing a narrow static inspection.
- Creating a small, clearly specified fixture or test.
- Checking a defined list of requirements.
- Formatting or updating a small documentation section.
- Reporting the results of a specified command.

Haiku should not own ambiguous, high-impact, architectural, or broad multi-step tasks.

Haiku's output is supporting work and must be checked when correctness matters.

## 3. Allocation principles

Choose the least costly model that can complete the task reliably.

| Work type | Allocation |
|---|---|
| Ambiguous, strategic, architectural, or high-risk decisions | Opus |
| Substantial implementation or multi-step investigation | Sonnet |
| Small, repetitive, clearly bounded subtasks | Haiku |
| Final review, integration, and verification | Opus |

Risk and ambiguity take precedence over task size. A small change with significant consequences should not automatically be delegated to Haiku.

Do not delegate work merely to use more agents. Opus should handle simple tasks directly when delegation would add overhead without meaningful benefit.

## 4. Delegation brief

Every delegated task must include:

1. **Task identifier**
2. **Assigned agent**
3. **Objective**
4. **Relevant context and references**
5. **Scope of work**
6. **Constraints and exclusions**
7. **Expected output**
8. **Acceptance criteria**
9. **Required checks or tests**
10. **Reporting requirements**

Use this template:

```text
TASK ID:
ASSIGNED AGENT:

OBJECTIVE:
[Specific result required.]

CONTEXT:
[Relevant information and references.]

SCOPE:
[Work to perform.]

CONSTRAINTS:
[Requirements to preserve and actions to avoid.]

EXPECTED OUTPUT:
[Deliverable or result.]

ACCEPTANCE CRITERIA:
[Observable conditions for completion.]

CHECKS:
[Tests, validation, or review required.]

REPORT BACK:
- Work completed
- Files or artifacts changed
- Checks performed and results
- Assumptions or uncertainties
- Remaining issues
```

Do not delegate an underspecified task when the missing information could materially change the result. Opus should clarify it or make only clearly labelled, low-risk assumptions.

## 5. Parallel work and file ownership

- Avoid assigning multiple agents to edit the same file or artifact simultaneously.
- Parallelize only tasks with clear boundaries and manageable dependencies.
- Agree on interfaces and expected outputs before parallel implementation.
- If agents share a working directory, serialize edits to overlapping files.
- Review and reconcile changes before integration.
- Never resolve conflicting changes by blindly choosing the newest output.

## 6. Review and verification

Before accepting delegated work, Opus must assess whether it:

- Answers the assigned task.
- Follows the user's requirements.
- Respects the stated scope and constraints.
- Is technically and factually sound.
- Includes the required evidence, tests, or validation.
- Introduces unintended changes or unsupported assumptions.
- Is complete enough to integrate or deliver.

Do not treat an agent's completion claim as proof. Verify important results directly.

Do not weaken tests, omit failures, or present unverified work as verified merely to report completion.

## 7. Handling uncertainty and disagreements

Agents must distinguish facts from assumptions, inferences, and unverified claims.

When agents disagree:

1. Identify the precise point of disagreement.
2. Compare the evidence and reasoning from each agent.
3. Verify against the relevant source material or tests where possible.
4. Have Opus make and document the decision.
5. Preserve unresolved uncertainty rather than inventing certainty.

If an agent discovers work outside its assigned scope, it should report the finding to Opus rather than silently expanding the task.

## 8. Completion and reporting

Each agent must report:

- Assigned task.
- Work completed.
- Files or artifacts changed.
- Checks or tests performed.
- Results and failures.
- Assumptions and limitations.
- Outstanding issues.

Opus decides whether delegated work is accepted and whether the overall task is complete.

**Final principle: Delegate execution appropriately, retain central coordination and accountability, and deliver what the user actually asked for.**
