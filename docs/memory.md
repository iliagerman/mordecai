# Memory System

Mordecai uses a two-layer memory architecture to provide both immediate conversation context and persistent long-term memory.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Session Memory (STM)                      │
│  SlidingWindowConversationManager                           │
│  - In-memory conversation history                            │
│  - Automatically trimmed to window_size                      │
│  - Cleared on new session                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ On session end or limit reached
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Memory Extraction Service                       │
│  - Extracts facts, preferences, summaries                    │
│  - Stores to AgentCore Memory                                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Long-Term Memory (LTM)                      │
│  AWS Bedrock AgentCore Memory                               │
│  - /facts/{actorId}: Learned information                     │
│  - /preferences/{actorId}: User preferences                  │
│  - /summaries/{actorId}/{sessionId}: Session summaries       │
└─────────────────────────────────────────────────────────────┘
```

## Session Memory

Session memory keeps the recent conversation context available to the agent during a conversation.

### Implementation

- Uses Strands SDK's `SlidingWindowConversationManager`
- Configurable window size via `conversation_window_size` (default: 20 messages)
- Automatically trims older messages when window is exceeded
- Completely in-memory, no persistence

### Lifecycle

1. **Start**: Empty when user begins a new session
2. **During conversation**: Messages added as user and agent interact
3. **Window management**: Oldest messages trimmed when limit reached
4. **End**: Cleared when user issues `new` command or conversation limit reached

## Long-Term Memory

Long-term memory persists important information across sessions using AWS Bedrock AgentCore Memory.

### Memory Strategies

AgentCore Memory uses three strategies to automatically extract and store information:

1. **Semantic Strategy** (`/facts/{actorId}`)
   - Extracts factual information from conversations
   - Examples: "User works at Acme Corp", "User's birthday is March 15"

2. **User Preference Strategy** (`/preferences/{actorId}`)
   - Learns user preferences from conversations
   - Examples: "Prefers concise responses", "Likes Python over JavaScript"

3. **Summary Strategy** (`/summaries/{actorId}/{sessionId}`)
   - Summarizes conversations at session end
   - Provides context from previous sessions

### Memory Retrieval

Long-term memories are retrieved in two ways:

1. **Automatic injection**: Relevant memories are retrieved based on the user's message and injected into the agent's system prompt
2. **On-demand search**: Agent can use `search_memory` tool when user asks about past conversations

## Memory Tools

### set_agent_name

Stores the agent's name in memory when a user assigns one.

```
User: "I'll call you Jarvis"
Agent: [uses set_agent_name tool with name="Jarvis"]
Agent: "I've stored my name as 'Jarvis' in memory. I'll remember this name across our conversations."
```

### search_memory

Searches long-term memory for facts and preferences.

```
User: "What do you know about me?"
Agent: [uses search_memory tool with query="user information facts"]
Agent: "Based on my memory, I know that:
- You work at Acme Corp
- You prefer Python for backend development
- Your birthday is March 15"
```

**Parameters:**
- `query` (required): Search query string
- `memory_type` (optional): "all", "facts", or "preferences" (default: "all")

## Configuration

### config.json

```json
{
  "memory_enabled": true,
  "memory_id": null,
   "memory_name": "MordecaiMemory",
   "memory_description": "Mordecai multi-user memory with strategies",
  "memory_retrieval_top_k": 10,
  "memory_retrieval_relevance_score": 0.2,
  "conversation_window_size": 20,
  "max_conversation_messages": 30,
  "extraction_timeout_seconds": 30
}
```

### Configuration Options

| Option                             | Description                     | Default          |
| ---------------------------------- | ------------------------------- | ---------------- |
| `memory_enabled`                   | Enable/disable memory system    | `true`           |
| `memory_id`                        | Existing AgentCore memory ID    | `null`           |
| `memory_name`                      | Name for new memory instance    | `MordecaiMemory` |
| `conversation_window_size`         | Messages in session window      | `20`             |
| `max_conversation_messages`        | Messages before auto-extraction | `30`             |
| `memory_retrieval_top_k`           | Max memories to retrieve        | `10`             |
| `memory_retrieval_relevance_score` | Min relevance (0.0-1.0)         | `0.2`            |

## User Isolation

Each user's memories are isolated via `actor_id` (derived from Telegram username/ID):

- `/facts/{actorId}` - User A's facts are separate from User B's
- `/preferences/{actorId}` - Preferences are per-user
- `/summaries/{actorId}/{sessionId}` - Summaries scoped to user and session

## Automatic Extraction

When the conversation reaches `max_conversation_messages`:

1. Extraction service analyzes the conversation
2. Facts and preferences are extracted and stored
3. Session is summarized
4. Conversation history is cleared
5. User is notified: "✨ Your conversation has been summarized and important information saved."

## First-Time Setup

On first startup with `memory_enabled=true`:

1. If `memory_id` is not set, a new AgentCore memory instance is created
2. Memory strategies are configured automatically
3. The memory ID is logged - save it to `config.json` for reuse

## Troubleshooting

### Memory not persisting

- Check `memory_enabled` is `true`
- Verify AWS credentials are configured
- Check logs for AgentCore Memory errors

### Agent not remembering name

- Ensure user explicitly assigns a name ("call yourself X", "your name is X")
- Check that `set_agent_name` tool is being invoked
- Verify memory service is available

### Old conversations replaying

This was fixed by separating session memory (SlidingWindowConversationManager) from long-term memory (AgentCore). Session memory is now purely in-memory and doesn't persist raw conversations.
