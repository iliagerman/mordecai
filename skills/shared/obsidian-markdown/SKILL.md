---
name: obsidian-markdown
description: Create and edit Obsidian Flavored Markdown with wikilinks, embeds, callouts, properties, and other Obsidian-specific syntax. Use when working with .md files in Obsidian, or when the user mentions wikilinks, callouts, frontmatter, tags, embeds, or Obsidian notes.
---

# Obsidian Flavored Markdown Skill

This skill enables skills-compatible agents to create and edit valid Obsidian Flavored Markdown, including all Obsidian-specific syntax extensions.

## Overview

Obsidian uses a combination of Markdown flavors:
- [CommonMark](https://commonmark.org/)
- [GitHub Flavored Markdown](https://github.github.com/gfm/)
- [LaTeX](https://www.latex-project.org/) for math
- Obsidian-specific extensions (wikilinks, callouts, embeds, etc.)

## Vaults in this deployment

This deployment uses **one** Obsidian vault directory.

The vault root is configured by the backend setting:

- `obsidian_vault_root` (env override: `AGENT_OBSIDIAN_VAULT_ROOT`)

In container deployments, this is commonly set to `/app/obsidian-vaults/`.

Important:
- Treat `/app/obsidian-vaults/` as a *common default*, not a guarantee.
- The authoritative vault root at runtime is whatever the backend config resolves
  for `obsidian_vault_root` / `AGENT_OBSIDIAN_VAULT_ROOT`.
- If you are unsure which path is active, ask the operator/user to confirm the
  resolved path *inside the running container*.

Within that single vault, top-level folders are used as categories/areas:

- `me/` (agent-owned root)
  - `me/[USER_ID]/` (per-user area; default write target)
- `family/`
- `work/`
- `personal/`
- `agentleague/`
- `tools/`

Selection rules:

- If the user explicitly names a folder/category (e.g. “in my work notes”), use that folder.
- If the user does not specify, default to `me/[USER_ID]/`.
- The agent must always scope reads/writes to the current user’s folder `me/[USER_ID]/` so information between users will not be mixed.
  - Never read/write another user’s folder (e.g. `me/<someone-else>/`) unless the user explicitly requests it *and* you can confirm it’s the same tenant/user.
  - If `[USER_ID]` is not known from the current context, ask for it before creating/modifying any files under `me/`.
- Do not modify content outside `me/[USER_ID]/` unless the user explicitly asks (or clearly implies) changes there.

Path conventions:

- When you propose creating/editing a note, include a concrete filesystem path like:
  `<VAULT_ROOT>/me/[USER_ID]/<relative-path>.md`
  where `<VAULT_ROOT>` is the configured `obsidian_vault_root`.

## Safe file discovery (when the exact note path is unknown)

If the user asks for a note but does not provide an exact filename/path (e.g. “what’s on my shopping list?”), and the vault is accessible at runtime, you should:

1. **Search within the expected folder(s)** (usually `family/` and/or `me/[USER_ID]/`).
2. **Keep searches bounded** (limit depth, limit results) and ask a clarifying question if multiple candidates match.
3. **Read the most likely candidates** (using file_read) until you find the requested content.

Recommended bounded search patterns (use shell tool):

- Find likely shopping list notes (names only):
  - `find <VAULT_ROOT>/family -maxdepth 3 -type f \( -iname '*shop*' -o -iname '*grocery*' -o -iname '*list*' \) -print | head -n 20`
  - `find <VAULT_ROOT>/me/[USER_ID] -maxdepth 4 -type f \( -iname '*shop*' -o -iname '*grocery*' -o -iname '*list*' \) -print | head -n 20`

- Content search (bounded; prefer ripgrep if available):
  - `rg -n --max-count 20 -S "shopping|grocery" <VAULT_ROOT>/family 2>/dev/null || true`

Then, for the top candidates:
- `file_read(path="/full/path/to/note.md", mode="view")`

If you cannot find a unique match:
- Ask the user to confirm the note name or folder, or to share one line from the note header.

## Personality files

Personality/identity instructions are stored as markdown files in the vault:

- Per-user:
  - `me/[USER_ID]/soul.md`
  - `me/[USER_ID]/id.md`

Default fallback templates live in the repo:

- `instructions/soul.md`
- `instructions/id.md`

When the per-user file is missing, the backend will fall back to the repo defaults under `instructions/`.
- For `obsidian://open` links:
  - `vault` should be the **vault name on that device** (it’s the folder name you opened in Obsidian).
  - `file` should be a path *within the vault*, e.g. `me/[USER_ID]/Notes/Welcome.md`.

## Basic Formatting

### Paragraphs and Line Breaks

```markdown
This is a paragraph.

This is another paragraph (blank line between creates separate paragraphs).

For a line break within a paragraph, add two spaces at the end  
or use Shift+Enter.
```

### Headings

```markdown
# Heading 1
## Heading 2
### Heading 3
#### Heading 4
##### Heading 5
###### Heading 6
```

### Text Formatting

| Style         | Syntax                   | Example           | Output          |
| ------------- | ------------------------ | ----------------- | --------------- |
| Bold          | `**text**` or `__text__` | `**Bold**`        | **Bold**        |
| Italic        | `*text*` or `_text_`     | `*Italic*`        | *Italic*        |
| Bold + Italic | `***text***`             | `***Both***`      | ***Both***      |
| Strikethrough | `~~text~~`               | `~~Striked~~`     | ~~Striked~~     |
| Highlight     | `==text==`               | `==Highlighted==` | ==Highlighted== |
| Inline code   | `` `code` ``             | `` `code` ``      | `code`          |

### Escaping Formatting

Use backslash to escape special characters:
```markdown
\*This won't be italic\*
\#This won't be a heading
1\. This won't be a list item
```

Common characters to escape: `\*`, `\_`, `\#`, `` \` ``, `\|`, `\~`

## Internal Links (Wikilinks)

### Basic Links

```markdown
[[Note Name]]
[[Note Name.md]]
[[Note Name|Display Text]]
```

### Link to Headings

```markdown
[[Note Name#Heading]]
[[Note Name#Heading|Custom Text]]
[[#Heading in same note]]
[[##Search all headings in vault]]
```

### Link to Blocks

```markdown
[[Note Name#^block-id]]
[[Note Name#^block-id|Custom Text]]
```

Define a block ID by adding `^block-id` at the end of a paragraph:
```markdown
This is a paragraph that can be linked to. ^my-block-id
```

For lists and quotes, add the block ID on a separate line:
```markdown
> This is a quote
> With multiple lines

^quote-id
```

### Search Links

```markdown
[[##heading]]     Search for headings containing "heading"
[[^^block]]       Search for blocks containing "block"
```

## Markdown-Style Links

```markdown
[Display Text](Note%20Name.md)
[Display Text](Note%20Name.md#Heading)
[Display Text](https://example.com)
[Note](obsidian://open?vault=VaultName&file=Note.md)
```

Note: Spaces must be URL-encoded as `%20` in Markdown links.

## Embeds

### Embed Notes

```markdown
![[Note Name]]
![[Note Name#Heading]]
![[Note Name#^block-id]]
```

### Embed Images

```markdown
![[image.png]]
![[image.png|640x480]]    Width x Height
![[image.png|300]]        Width only (maintains aspect ratio)
```

### External Images

```markdown
![Alt text](https://example.com/image.png)
![Alt text|300](https://example.com/image.png)
```

### Embed Audio

```markdown
![[audio.mp3]]
![[audio.ogg]]
```

### Embed PDF

```markdown
![[document.pdf]]
![[document.pdf#page=3]]
![[document.pdf#height=400]]
```

### Embed Lists

```markdown
![[Note#^list-id]]
```

Where the list has been defined with a block ID:
```markdown
- Item 1
- Item 2
- Item 3

^list-id
```

### Embed Search Results

````markdown
```query
tag:#project status:done
```
````

## Callouts

### Basic Callout

```markdown
> [!note]
> This is a note callout.

> [!info] Custom Title
> This callout has a custom title.

> [!tip] Title Only
```

### Foldable Callouts

```markdown
> [!faq]- Collapsed by default
> This content is hidden until expanded.

> [!faq]+ Expanded by default
> This content is visible but can be collapsed.
```

### Nested Callouts

```markdown
> [!question] Outer callout
> > [!note] Inner callout
> > Nested content
```

### Supported Callout Types

| Type       | Aliases                | Description           |
| ---------- | ---------------------- | --------------------- |
| `note`     | -                      | Blue, pencil icon     |
| `abstract` | `summary`, `tldr`      | Teal, clipboard icon  |
| `info`     | -                      | Blue, info icon       |
| `todo`     | -                      | Blue, checkbox icon   |
| `tip`      | `hint`, `important`    | Cyan, flame icon      |
| `success`  | `check`, `done`        | Green, checkmark icon |
| `question` | `help`, `faq`          | Yellow, question mark |
| `warning`  | `caution`, `attention` | Orange, warning icon  |
| `failure`  | `fail`, `missing`      | Red, X icon           |
| `danger`   | `error`                | Red, zap icon         |
| `bug`      | -                      | Red, bug icon         |
| `example`  | -                      | Purple, list icon     |
| `quote`    | `cite`                 | Gray, quote icon      |

### Custom Callouts (CSS)

```css
.callout[data-callout="custom-type"] {
  --callout-color: 255, 0, 0;
  --callout-icon: lucide-alert-circle;
}
```

## Lists

### Unordered Lists

```markdown
- Item 1
- Item 2
  - Nested item
  - Another nested
- Item 3

* Also works with asterisks
+ Or plus signs
```

### Ordered Lists

```markdown
1. First item
2. Second item
   1. Nested numbered
   2. Another nested
3. Third item

1) Alternative syntax
2) With parentheses
```

### Task Lists

```markdown
- [ ] Incomplete task
- [x] Completed task
- [ ] Task with sub-tasks
  - [ ] Subtask 1
  - [x] Subtask 2
```

## Quotes

```markdown
> This is a blockquote.
> It can span multiple lines.
>
> And include multiple paragraphs.
>
> > Nested quotes work too.
```

## Code

### Inline Code

```markdown
Use `backticks` for inline code.
Use double backticks for ``code with a ` backtick inside``.
```

### Code Blocks

````markdown
```
Plain code block
```

```javascript
// Syntax highlighted code block
function hello() {
  console.log("Hello, world!");
}
```

```python
# Python example
def greet(name):
    print(f"Hello, {name}!")
```
````

### Nesting Code Blocks

Use more backticks or tildes for the outer block:

`````markdown
````markdown
Here's how to create a code block:
```js
console.log("Hello")
```
````
`````

## Tables

```markdown
| Header 1 | Header 2 | Header 3 |
| -------- | -------- | -------- |
| Cell 1   | Cell 2   | Cell 3   |
| Cell 4   | Cell 5   | Cell 6   |
```

### Alignment

```markdown
| Left | Center | Right |
| :--- | :----: | ----: |
| Left | Center | Right |
```

### Using Pipes in Tables

Escape pipes with backslash:
```markdown
| Column 1          | Column 2        |
| ----------------- | --------------- |
| [[Link\|Display]] | ![[Image\|100]] |
```

## Math (LaTeX)

### Inline Math

```markdown
This is inline math: $e^{i\pi} + 1 = 0$
```

### Block Math

```markdown
$$
\begin{vmatrix}
a & b \\
c & d
\end{vmatrix} = ad - bc
$$
```

### Common Math Syntax

```markdown
$x^2$              Superscript
$x_i$              Subscript
$\frac{a}{b}$      Fraction
$\sqrt{x}$         Square root
$\sum_{i=1}^{n}$   Summation
$\int_a^b$         Integral
$\alpha, \beta$    Greek letters
```

## Diagrams (Mermaid)

````markdown
```mermaid
graph TD
    A[Start] --> B{Decision}
    B -->|Yes| C[Do this]
    B -->|No| D[Do that]
    C --> E[End]
    D --> E
```
````

### Sequence Diagrams

````markdown
```mermaid
sequenceDiagram
    Alice->>Bob: Hello Bob
    Bob-->>Alice: Hi Alice
```
````

### Linking in Diagrams

````markdown
```mermaid
graph TD
    A[Biology]
    B[Chemistry]
    A --> B
    class A,B internal-link;
```
````

## Footnotes

```markdown
This sentence has a footnote[^1].

[^1]: This is the footnote content.

You can also use named footnotes[^note].

[^note]: Named footnotes still appear as numbers.

Inline footnotes are also supported.^[This is an inline footnote.]
```

## Comments

```markdown
This is visible %%but this is hidden%% text.

%%
This entire block is hidden.
It won't appear in reading view.
%%
```

## Horizontal Rules

```markdown
---
***
___
- - -
* * *
```

## Properties (Frontmatter)

Properties use YAML frontmatter at the start of a note:

```yaml
---
title: My Note Title
date: 2024-01-15
tags:
  - project
  - important
aliases:
  - My Note
  - Alternative Name
cssclasses:
  - custom-class
status: in-progress
rating: 4.5
completed: false
due: 2024-02-01T14:30:00
---
```

### Property Types

| Type        | Example                         |
| ----------- | ------------------------------- |
| Text        | `title: My Title`               |
| Number      | `rating: 4.5`                   |
| Checkbox    | `completed: true`               |
| Date        | `date: 2024-01-15`              |
| Date & Time | `due: 2024-01-15T14:30:00`      |
| List        | `tags: [one, two]` or YAML list |
| Links       | `related: "[[Other Note]]"`     |

### Default Properties

- `tags` - Note tags
- `aliases` - Alternative names for the note
- `cssclasses` - CSS classes applied to the note

## Tags

```markdown
#tag
#nested/tag
#tag-with-dashes
#tag_with_underscores

In frontmatter:
---
tags:
  - tag1
  - nested/tag2
---
```

Tags can contain:
- Letters (any language)
- Numbers (not as first character)
- Underscores `_`
- Hyphens `-`
- Forward slashes `/` (for nesting)

## HTML Content

Obsidian supports HTML within Markdown:

```markdown
<div class="custom-container">
  <span style="color: red;">Colored text</span>
</div>

<details>
  <summary>Click to expand</summary>
  Hidden content here.
</details>

<kbd>Ctrl</kbd> + <kbd>C</kbd>
```

## Complete Example

````markdown
---
title: Project Alpha
date: 2024-01-15
tags:
  - project
  - active
status: in-progress
priority: high
---

# Project Alpha

## Overview

This project aims to [[improve workflow]] using modern techniques.

> [!important] Key Deadline
> The first milestone is due on ==January 30th==.

## Tasks

- [x] Initial planning
- [x] Resource allocation
- [ ] Development phase
  - [ ] Backend implementation
  - [ ] Frontend design
- [ ] Testing
- [ ] Deployment

## Technical Notes

The main algorithm uses the formula $O(n \log n)$ for sorting.

```python
def process_data(items):
    return sorted(items, key=lambda x: x.priority)
```

## Architecture

```mermaid
graph LR
    A[Input] --> B[Process]
    B --> C[Output]
    B --> D[Cache]
```

## Related Documents

- ![[Meeting Notes 2024-01-10#Decisions]]
- [[Budget Allocation|Budget]]
- [[Team Members]]

## References

For more details, see the official documentation[^1].

[^1]: https://example.com/docs

%%
Internal notes:
- Review with team on Friday
- Consider alternative approaches
%%
````

## References

- [Basic formatting syntax](https://help.obsidian.md/syntax)
- [Advanced formatting syntax](https://help.obsidian.md/advanced-syntax)
- [Obsidian Flavored Markdown](https://help.obsidian.md/obsidian-flavored-markdown)
- [Internal links](https://help.obsidian.md/links)
- [Embed files](https://help.obsidian.md/embeds)
- [Callouts](https://help.obsidian.md/callouts)
- [Properties](https://help.obsidian.md/properties)
