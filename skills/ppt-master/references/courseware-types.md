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
- **Scene fidelity**: if the source describes a classroom scene, character action,
  prop, photo, comic frame, or emotional moment, that description is a visual
  asset requirement. Do not reduce it to a text card.

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
- If the example is a story / classroom case / character moment, include an
  AI-generated or user-provided scene illustration in `layer-context` or
  `layer-content`. The image resource row must preserve the original scene
  description rather than summarizing it away.

### quiz

- Structure: question → student action → answer hidden in `layer-feedback`.
- `layer-feedback` may be visually present but should be easy to animate/reveal later.
- Speaker notes must include reference answer, explanation, common wrong answer, and teacher prompt.
- When the quiz asks students to react to a concrete scene, generate or source
  that scene as a visual stimulus before asking the question.

## 4.1 Scene Illustration Requirements

Courseware pages often contain visual instructions such as "一张拍立得照片，内容
是女孩低着头，脸红到了耳根" or "操场上同学们垂头丧气". These are not decorative
phrases; they are teaching stimuli.

Strategist must add an `Image Resource List` row with `Acquire Via: ai` for:

- Polaroid / photo / comic frame / blackboard scene descriptions.
- Character actions, facial expressions, or classroom episodes.
- Props that carry meaning: thermometer, balance scale, archive wall, phone call,
  sticky notes, stage, worksheet, etc.
- Any page where the source says "插图", "照片", "画面", "场景", "数字人", or
  describes visual placement in detail.

Executor must not replace such rows with generic icons or text cards. If Step 5
cannot produce the image, the page should keep a clearly sized image placeholder
and the deck should stop at the image-readiness gate before final export unless
the user explicitly accepts placeholder output.

## 4.2 Design Brief Parsing Rules

Courseware source documents often mix layout instructions and student-facing
copy in the same bullet:

- `档案袋文字：右侧放置一个打开的信封/档案袋图形，里面写着：“……”`
- `数字人对话框：豆包侦探气泡框：“……”`
- `知识点标签：底部标签：“……”`

The label before the colon describes the design role, not necessarily visible
copy. Strategist and Executor must separate:

- **Visual container / role**: `档案袋文字`, `数字人对话框`, `知识点标签`, `左侧`,
  `右侧`, `排版要求`, `动效提示` describe where and how to render content.
- **Visible student-facing copy**: quoted text, listed labels, task prompts,
  knowledge-point sentences, and case narratives are what should appear on the
  slide.

Do not turn design-role labels into component headings unless the source clearly
asks for that exact label to be visible. For the example above, render an open
envelope / file-bag shape and place the quoted case narrative inside it; do not
show a large heading called `档案袋文字` or `档案袋`.

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
