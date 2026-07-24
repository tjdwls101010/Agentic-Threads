# Output Schema

Every read result uses one of three documented objects:

- `feed`, `fetch`, `post`, and `search --type posts` emit top-level `Post` objects.
- `followers`, `following`, and `search --type people` emit top-level `User` objects.
- Every `Post.author` is a `User`, and every `Post.media` element is a `Media`.

The installed schema is authoritative:

```bash
agentic-threads schema          # annotated field listing
agentic-threads schema --json   # JSON Schema draft 2020-12
```

The schema is generated from the same serialization contract that writes result files. Except for `raw`, every documented key is always present. An unavailable scalar or nested value is JSON `null`; `Post.media` is always an array, `Post.text` and identifiers are strings, and ordinary boolean fields are always booleans. `User.is_verified` can be `null` when Threads does not supply the state.

## `Post`

| Field | JSON type | Null/optional? | Meaning |
|---|---|---|---|
| `id` | string | never null | Threads' numeric post primary key, serialized as a string. Use it as the stable deduplication key. |
| `code` | string | nullable | The post's URL shortcode. |
| `url` | string | nullable | Canonical post URL when it can be formed. |
| `created_at` | string | nullable | Post creation time as ISO 8601 UTC with a `Z` suffix. This is the event time used by date filters. |
| `text` | string | never null | Caption/body text; an empty post uses `""`. |
| `author` | `User` | nullable | Normalized author record. |
| `like_count` | integer | nullable | Like count as exposed by Threads. `null` means unknown, not zero. |
| `reply_count` | integer | nullable | Reply count as exposed by Threads. |
| `repost_count` | integer | nullable | Repost count as exposed by Threads. |
| `quote_count` | integer | nullable | Quote count as exposed by Threads. |
| `media` | array of `Media` | never null | Attached media; `[]` when none is present. |
| `is_reply` | boolean | never null | Whether this object is a reply. |
| `reply_to_id` | string | nullable | Immediate parent post id when this is a reply. |
| `root_post_id` | string | nullable | Root post id for the reply thread when known. |
| `quoted_post` | `Post` | nullable | Quoted post, normalized with this same `Post` schema. |
| `reposted_post` | `Post` | nullable | Reposted post, normalized with this same `Post` schema. |
| `link_preview` | object | nullable | Link card as `{ "url": string, "title": string|null }`; `null` when no preview URL is available. |
| `is_pinned` | boolean | never null | Whether this post was delivered as a pinned profile post. |
| `captured_at` | string | never null | When this tool captured the response, as ISO 8601 UTC with a `Z` suffix. It is not the post's event time. |
| `raw` | object | optional key | Source GraphQL node, present only with `--raw`. Redacted unless `--no-redact` is also set. |

`quoted_post` and `reposted_post` are recursive `Post` values. Consumers must not assume they are always present, and should use `id` rather than `captured_at` when merging repeat captures.

## `User`

| Field | JSON type | Null? | Meaning |
|---|---|---|---|
| `id` | string | never null | Threads' numeric user primary key, serialized as a string. |
| `username` | string | never null | Account username without a leading `@`. |
| `full_name` | string | nullable | Display name. |
| `is_verified` | boolean | nullable | Verification state; `null` means the payload did not expose it. |
| `follower_count` | integer | nullable | Follower count. |
| `following_count` | integer | nullable | Following count when the relevant profile payload exposes it. |
| `post_count` | integer | nullable | Post count when the relevant profile payload exposes it. |
| `bio` | string | nullable | Profile biography. |
| `profile_pic_url` | string | nullable | Profile image URL. Treat signed CDN URLs as sensitive and temporary. |
| `url` | string | nullable | Canonical Threads profile URL when it can be formed. |
| `raw` | object | optional key | Source GraphQL user node, present only with `--raw`. Redacted unless `--no-redact` is also set. |

The top-level `User` written by `followers`, `following`, or people search is the same shape used under `Post.author`. A `User` has no post text or event date; feed its `username` or `id` to a separate `fetch` invocation when you need posts.

Like `Post.raw`, `User.raw` is diagnostics-only and absent from normal output.

## `Media`

| Field | JSON type | Null? | Meaning |
|---|---|---|---|
| `kind` | string enum | never null | One of `photo`, `video`, `carousel`, or `unknown`. |
| `url` | string | never null | Media URL supplied by Threads. Signed CDN URLs can expire and can disclose access context. |
| `width` | integer | nullable | Pixel width when available. |
| `height` | integer | nullable | Pixel height when available. |
| `alt_text` | string | nullable | Creator-provided or platform-provided accessibility text when available. |

A carousel can produce multiple `Media` elements. Do not assume a `kind` value predicts the number of elements in `Post.media`.

## JSON and NDJSON framing

`--format json` is the default and writes one top-level array:

```json
[
  {"id": "1001", "text": "First synthetic post"},
  {"id": "1002", "text": "Second synthetic post"}
]
```

The abbreviated objects above illustrate framing only; real objects contain every documented key except optional `raw`.

`--format ndjson` writes the same serialized objects one per line, with no enclosing array:

```text
{"id":"1001","text":"First synthetic post","...":"..."}
{"id":"1002","text":"Second synthetic post","...":"..."}
```

The format changes only the outer framing. It does not change field names or values.

## Complete synthetic example

This example is synthetic and contains no captured Threads data:

```json
{
  "id": "1001",
  "code": "SyntheticCode1",
  "url": "https://www.threads.com/@synthetic_alice/post/SyntheticCode1",
  "created_at": "2026-07-01T12:00:00Z",
  "text": "A synthetic Threads post used only to document the schema.",
  "author": {
    "id": "9001",
    "username": "synthetic_alice",
    "full_name": "Synthetic Alice",
    "is_verified": false,
    "follower_count": 12,
    "following_count": 3,
    "post_count": 8,
    "bio": "Synthetic profile for offline documentation.",
    "profile_pic_url": "https://media.example.test/avatar.jpg",
    "url": "https://www.threads.com/@synthetic_alice"
  },
  "like_count": 5,
  "reply_count": 1,
  "repost_count": 0,
  "quote_count": 0,
  "media": [
    {
      "kind": "photo",
      "url": "https://media.example.test/synthetic.jpg",
      "width": 1200,
      "height": 800,
      "alt_text": "A synthetic landscape"
    }
  ],
  "is_reply": false,
  "reply_to_id": null,
  "root_post_id": "1001",
  "quoted_post": null,
  "reposted_post": null,
  "link_preview": {
    "url": "https://example.test/article",
    "title": "Synthetic article"
  },
  "is_pinned": false,
  "captured_at": "2026-07-23T09:30:00Z"
}
```

## Reply-thread ordering

`agentic-threads post <url|id>` includes the reply thread by default. Its output array starts with the requested root `Post`; later objects are replies with `is_reply: true`. Use `reply_to_id` for the immediate edge and `root_post_id` for the thread root. `--no-replies` suppresses the reply objects.

This differs from `agentic-threads fetch <user>`, where replies-tab posts are excluded unless `--replies` is supplied.

## Date fields

- `created_at` answers when the post was created. It can be `null` if the source timestamp is missing or unparseable.
- `captured_at` answers when the GraphQL response was captured by this tool. It is always present and normally changes between runs.
- Both serialize in UTC ISO 8601 form with a `Z` suffix.
- `--since` and `--until` compare Post event dates, not capture dates. For `search --type posts`, those bounds are applied client-side to results returned by the live GraphQL search.

## The `raw` field

Normal output contains only normalized fields. `--raw` adds the underlying GraphQL node as `raw` to each object for parser debugging. The attached node is recursively scrubbed by default; `--raw --no-redact` disables that protection and prints a warning.

The normalized fields you asked to retrieve are not anonymized. Even redacted `raw` data can contain unexpected personal data because redaction is pattern-based, not a safety certification. Treat every output file as sensitive, never commit captures, and use synthetic minimal examples in reports. See [Security and Privacy](Security-and-Privacy.md).
