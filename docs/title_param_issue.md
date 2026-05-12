# Title Parameter Issue in create_conversation

## Problem

`ConversationStore.create_conversation(title=...)` is dead weight. Whatever you pass
gets overwritten by `_extract_title(messages)` inside `save_messages` — which fires on
the very next line or shortly after. The `title` param just creates a brief window
where a wrong title is visible, then silently replaced.

## Three call sites, all broken differently

| Call site | What it passes | What happens |
|-----------|---------------|--------------|
| `proxy_chat` (api.py:545) | `title=clean_title[:80]` — user's first message, stripped | `save_frontend_messages` runs right after but doesn't extract title. Eventually `save_messages` fixes it. Mostly correct, just redundant. |
| Continuation (api.py:258) | `title=args.get("prompt", ...)[:80]` — agent's free-form prompt | `save_messages` runs on the VERY NEXT LINE and overwrites with the real title from the user task. The manual title lives for ~2 lines of code. |
| `save_conversation` (api.py:688) | `title` not passed — defaults to `"Untitled"` | `save_messages` runs 2 lines later and fixes it. |

## The fix

1. Remove `title` parameter from `create_conversation`. Initial title = `""`.
2. `save_frontend_messages`: if title is empty, extract from the messages just saved and update the index.
3. `save_messages`: already extracts title (no change needed).
4. Drop all manual title computation at call sites.

`_extract_title` already strips task instruction markers — so the stripping in `proxy_chat` is also redundant.

## Why it matters

In the continuation path, if `save_messages` somehow throws, the sidebar shows
agent gibberish (`"The previous agent session accomplished the following: we set up..."`)
instead of the actual user task. With the fix, it'd show empty string which is more
honest as "not yet titled."
