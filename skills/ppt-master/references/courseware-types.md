# Courseware Page Type System

This reference defines the vertical K-12 / teen education mode. Use it when a
deck is explicitly a courseware deck, lesson deck, teaching PPT, classroom
material, training lesson for teenagers, or when `spec_lock.md` declares
`courseware.mode: enabled`.

## 1. Courseware Goal

Courseware is not a content showcase. Every slide must help a learner move from
not knowing to knowing, from knowing to applying, or from applying to reflecting.

Core priorities:

- **Knowledge accuracy**: no invented facts, formulas, examples, or historical data.
- **Teaching effectiveness**: use teaching objectives, examples, practice, and summary.
- **Cognitive load control**: one teaching move per slide whenever possible.
- **Interaction design**: every lesson segment should include teacher prompts or student action.
- **Layered reveal readiness**: slide elements must be organized into semantic layers for animation, editing, and staged teaching.

## 2. Page Types

| Type ID | Name | Purpose | Default Rhythm | Per Lesson |
| ------- | ---- | ------- | -------------- | ---------- |
| `objectives` | Teaching Objectives | Show what students will learn and do | `anchor` | 1 |
| `concept` | Concept Explanation | Teach one core knowledge point | `breathing` | 4-6 |
| `example` | Worked Example | Demonstrate a typical problem or case | `dense` | 1-2 |
| `quiz` | In-class Practice | Let students try and receive feedback | `breathing` | 1-2 |
| `comparison` | Comparison / Disambiguation | Contrast similar concepts or methods | `dense` | 0-1 |
| `diagram` | Diagram / Data | Visualize relationships, processes, or data | `dense` | 0-1 |
| `summary` | Knowledge Summary | Review knowledge points and learning path | `anchor` | 1 |

## 3. Required Metadata Per Slide

Each courseware slide in `design_spec.md §IX` must include:

- `courseware_type`: one of the type IDs above
- `knowledge_point_id`: `KPxx`, or `none` for objectives / summary
- `difficulty`: `easy` / `medium` / `hard`
- `bloom_level`: `remember` / `understand` / `apply` / `analyze` / `evaluate` / `create`
- `estimated_minutes`: integer
- `teaching_move`: a one-sentence description of what happens in class
- `layer_plan`: which layer groups the Executor must create

## 4. Page Type Requirements

### objectives

- Shows 3-5 measurable objectives.
- Use verbs students can act on: identify, explain, solve, compare, design.
- Avoid vague objectives such as "understand deeply" without observable output.
- Speaker notes include the lesson hook and success criteria.

### concept

- Teaches exactly one new concept or one tightly bound concept cluster.
- Structure: concept name → definition → intuition / analogy → 2-3 key points.
- Same slide must not introduce more than 3 new terms.
- Use `layer-interaction` for a quick check question when possible.

### example

- Structure: problem area → solving steps → key step highlight → answer / takeaway.
- Solving steps max: 5.
- Highlight the reasoning step, not just the final answer.
- Speaker notes include common mistakes.

### quiz

- Structure: question → student action → answer hidden in `layer-feedback`.
- `layer-feedback` may be visually present but should be easy to animate/reveal later.
- Speaker notes must include reference answer, explanation, common wrong answer, and teacher prompt.

### comparison

- Compare 2-4 concepts, methods, or positions.
- Each column/row must use the same criteria.
- End with "when to use which" guidance.

### diagram

- Use when relationships are spatial: process, hierarchy, cause-effect, timeline, system.
- Labels must be short; explanation belongs in notes or side annotations.
- If the diagram is data-based, preserve numerical accuracy and use chart markers as normal.

### summary

- Reviews all core knowledge points, not only slide titles.
- Uses a map, checklist, or grid that supports retrieval practice.
- Speaker notes include a final reflective or transfer question.

## 5. Lesson Structure Heuristic

For a single 40-45 minute lesson:

1. `objectives`
2. 2-3 `concept` slides
3. 1 `example` or `comparison`
4. 1 `quiz`
5. 2-3 `concept` / `diagram` slides
6. 1 `example`
7. 1 `quiz`
8. `summary`

For shorter 15-20 minute micro-lessons, keep:

- 1 objectives
- 2-3 concepts
- 1 quiz
- 1 summary

## 6. Cognitive Load Rules

- New terms per slide: max 3.
- Bullet items per visible layer: max 5.
- Worked-example steps: max 5.
- Quiz options: 2-4.
- Switch teaching mode every 5-7 minutes: explanation → example → practice → summary.

## 7. Speaker Notes Rules

Courseware notes are teacher-facing, not only narration. They may include:

- Teacher prompt
- Expected student response
- Reference answer
- Common misconception
- Follow-up question

For TTS/video export, convert these notes into spoken form first; do not feed teacher-only notes directly to TTS.
