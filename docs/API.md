# API Documentation

Mordecai exposes a REST API for task management and webhook integration.

Base URL: `http://localhost:8000`

## Health Check

### GET /health

Check if the service is running.

**Response**

```json
{
  "status": "healthy",
  "service": "mordecai"
}
```

---

## Task Endpoints

### GET /api/tasks/{user_id}

Get all tasks for a user grouped by status.

**Parameters**

| Name      | Type   | Location | Description                  |
| --------- | ------ | -------- | ---------------------------- |
| `user_id` | string | path     | The user's unique identifier |

**Response** `200 OK`

```json
{
  "pending": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "userId": "user123",
      "title": "Review PR",
      "description": "Review the authentication PR",
      "status": "pending",
      "createdAt": "2026-01-28T10:00:00Z",
      "updatedAt": "2026-01-28T10:00:00Z"
    }
  ],
  "inProgress": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "userId": "user123",
      "title": "Fix bug #42",
      "description": "",
      "status": "in_progress",
      "createdAt": "2026-01-27T09:00:00Z",
      "updatedAt": "2026-01-28T08:00:00Z"
    }
  ],
  "done": []
}
```

---

### POST /api/tasks

Create a new task.

**Request Body**

```json
{
  "userId": "user123",
  "title": "New task title",
  "description": "Optional description"
}
```

| Field         | Type   | Required | Description                  |
| ------------- | ------ | -------- | ---------------------------- |
| `userId`      | string | Yes      | The user's unique identifier |
| `title`       | string | Yes      | Task title (cannot be empty) |
| `description` | string | No       | Task description             |

**Response** `200 OK`

```json
{
  "taskId": "550e8400-e29b-41d4-a716-446655440002",
  "status": "created"
}
```

**Error Responses**

- `400 Bad Request` - Invalid request (empty title, user not found)

```json
{
  "detail": "Task title cannot be empty"
}
```

---

### PATCH /api/tasks/{task_id}/status

Update a task's status.

**Parameters**

| Name      | Type   | Location | Description                  |
| --------- | ------ | -------- | ---------------------------- |
| `task_id` | string | path     | The task's unique identifier |
| `user_id` | string | query    | User ID for authorization    |

**Request Body**

```json
{
  "status": "in_progress"
}
```

| Field    | Type   | Required | Description                                     |
| -------- | ------ | -------- | ----------------------------------------------- |
| `status` | string | Yes      | New status: `pending`, `in_progress`, or `done` |

**Response** `200 OK`

```json
{
  "taskId": "550e8400-e29b-41d4-a716-446655440000",
  "status": "updated"
}
```

**Error Responses**

- `404 Not Found` - Task not found

```json
{
  "detail": "Task 550e8400-e29b-41d4-a716-446655440000 not found"
}
```

- `403 Forbidden` - User doesn't own the task

```json
{
  "detail": "Cannot update another user's task"
}
```

---

## Webhook Endpoints

### POST /webhook

Receive external webhook events.

**Request Body**

```json
{
  "eventType": "task_created",
  "payload": {
    "user_id": "user123",
    "title": "New task from external system"
  },
  "timestamp": "2026-01-28T10:00:00Z",
  "source": "external-system"
}
```

| Field       | Type     | Required | Description                                      |
| ----------- | -------- | -------- | ------------------------------------------------ |
| `eventType` | string   | Yes      | Event type: `task_created` or `external_trigger` |
| `payload`   | object   | Yes      | Event-specific data                              |
| `timestamp` | datetime | Yes      | Event timestamp (ISO 8601)                       |
| `source`    | string   | No       | Source system identifier                         |

**Response** `200 OK`

```json
{
  "status": "success",
  "result": {
    "handled": true,
    "task_title": "New task from external system"
  }
}
```

**Error Responses**

- `400 Bad Request` - Invalid event format

```json
{
  "detail": "Invalid event type"
}
```

- `500 Internal Server Error` - Processing error

```json
{
  "detail": "Error processing webhook"
}
```

---

## Data Types

### TaskStatus

| Value         | Description                       |
| ------------- | --------------------------------- |
| `pending`     | Task is waiting to be started     |
| `in_progress` | Task is currently being worked on |
| `done`        | Task is completed                 |

### WebhookEventType

| Value              | Description                       |
| ------------------ | --------------------------------- |
| `task_created`     | A new task was created externally |
| `external_trigger` | Generic external trigger event    |

---

## Error Handling

All error responses follow this format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

### HTTP Status Codes

| Code  | Description                        |
| ----- | ---------------------------------- |
| `200` | Success                            |
| `400` | Bad Request - Invalid input        |
| `403` | Forbidden - Permission denied      |
| `404` | Not Found - Resource doesn't exist |
| `500` | Internal Server Error              |

---

## OpenAPI/Swagger

When the application is running, interactive API documentation is available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

---

## Memory Behavior

The platform uses AWS Bedrock AgentCore Memory to provide persistent memory capabilities. This section describes how memory affects agent behavior.

### Memory Types

| Type       | Scope   | Description                                                    |
| ---------- | ------- | -------------------------------------------------------------- |
| Short-term | Session | Conversation context within a single session                   |
| Long-term  | User    | Preferences, facts, and summaries that persist across sessions |

### How Memory Works

1. **Within a Session**: The agent remembers all messages exchanged in the current conversation
2. **Across Sessions**: The agent retrieves relevant long-term memories (preferences, facts, past summaries) when starting a new session
3. **User Isolation**: Each user's memories are completely isolated via `actor_id`

### Memory-Related Commands

| Command | Effect on Memory                                                             |
| ------- | ---------------------------------------------------------------------------- |
| `new`   | Starts a fresh session (clears short-term memory, long-term memory persists) |

### Memory Retrieval

When a user sends a message, the agent automatically:
1. Retrieves relevant user preferences from `/preferences/{actorId}`
2. Retrieves relevant facts from `/facts/{actorId}`
3. Retrieves recent session summaries from `/summaries/{actorId}/{sessionId}`

Retrieval is controlled by:
- `memory_retrieval_top_k`: Maximum number of memories to retrieve (default: 10)
- `memory_retrieval_relevance_score`: Minimum relevance threshold (default: 0.5)

### Memory Storage

At the end of each session or during conversation, the agent automatically:
1. Extracts and stores user preferences
2. Extracts and stores important facts
3. Creates session summaries

### Disabling Memory

Set `memory_enabled: false` in configuration to disable AgentCore memory. The agent will still function but without persistent memory across sessions.

---

## Agent Tools

The agent has access to built-in tools that extend its capabilities.

### set_agent_name

Stores the agent's name in memory when a user assigns one.

**When it's used**: The agent automatically calls this tool when a user says things like:
- "Your name is Mordecai"
- "I'll call you Jarvis"
- "Call yourself Atlas"

**How it works**: Creates a memory event with the name assignment that the memory strategies (semantic, preference) will extract and store as a long-term fact.

**Parameters**

| Name   | Type   | Required | Description                               |
| ------ | ------ | -------- | ----------------------------------------- |
| `name` | string | Yes      | The name the user wants to call the agent |

**Example interaction**:
```
User: "I want to call you Mordecai"
Agent: [calls set_agent_name with name="Mordecai"]
Agent: "I've stored my name as 'Mordecai' in memory. I'll remember this name across our conversations."
```

After a `/new` command (new session), the agent will retrieve the stored name from long-term memory and continue using it.
