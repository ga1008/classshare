# AI Prompt Pool Guidelines

LanShare has a shared prompt pool for reusable AI prompt snippets. It is for
non-chat AI workflows only. Direct AI assistant chats, classroom discussion
messages, and `window.prompt(...)` utility dialogs must not be added to this
pool.

## Product Rules

- The pool is partitioned by `feature_key`, not by user. Examples:
  `teacher_evaluation.rewrite_analysis`, `materials.ai_generate`,
  `exam.generate_scope`.
- Every non-chat AI prompt input must show a default-checked share option.
  Unchecking it means the prompt is never recorded.
- A recorded prompt stores only `feature_key`, prompt text, first entered time,
  and `use_count`. It must not store author identity, user id, organization id,
  class id, or per-user history.
- Recording is deduplicated per feature by the normalized prompt text hash. A
  duplicate prompt increments `use_count` on the existing row instead of adding
  a second row.
- Suggestions show the top 20 prompts for the current feature, ordered by
  `use_count DESC, created_at DESC`. Empty input shows the top 20; typing uses
  multi-term fuzzy `LIKE` search within the same feature.
- Suggestions must render below the input and share checkbox, not as an overlay
  that covers the text area.
- Suggestions must be usable by mouse and keyboard. `ArrowUp` / `ArrowDown`
  move through visible suggestions, `Enter` applies the active suggestion, and
  `Escape` closes the panel.

## Backend Contract

- Schema: `classroom_app/db/schema_prompt_pool.py`
- Service: `classroom_app/services/prompt_pool_service.py`
- API:
  - `GET /api/prompt-pool?feature_key=...&q=...&limit=20`
  - `POST /api/prompt-pool/record`

Use `record_prompt_if_shared(conn, feature_key, prompt, share)` when a backend
AI endpoint already receives the share flag. For frontend-only recording, record
after the relevant business request succeeds.

Schema creation belongs to application startup through `init_database()`. Do
not call schema initialization in high-frequency prompt-pool read/write paths;
tests that use an in-memory database should initialize the prompt-pool schema
explicitly.

## Frontend Contract

For ordinary inputs:

```html
<textarea data-prompt-pool-key="feature.scope"></textarea>
```

Then enhance the input after it exists:

```js
import {
    enhancePromptPoolInput,
    recordPromptForInput,
} from './prompt_pool.js';

const input = document.querySelector('[data-prompt-pool-key]');
enhancePromptPoolInput(input);

// Only after the AI task is successfully created or completed:
await recordPromptForInput(input);
```

For dynamic modal content, call `enhancePromptPoolInputs(modalElement)` after
rendering. For reusable menu + form modals, prefer `tree_select_form_modal.js`
and pass `promptPoolKey`.

Do not record on failed AI calls, validation failures, or canceled dialogs.

`prompt_pool.js` keeps a short client-side suggestion cache per feature/query
so repeated focus events do not flood the API. Successful recording invalidates
the current feature cache so updated usage counts appear on the next open.
