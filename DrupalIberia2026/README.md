# Drupal AI Agent with Elasticsearch

Build a RAG-powered AI chatbot inside Drupal that answers questions from
your indexed content using Elasticsearch kNN dense vectors and an
OpenAI-compatible LLM.

```
User question → Drupal AI Assistant → RAG action
  → Search API (files index) → Elasticsearch kNN dense vectors
  → LLM (LM Studio / LiteLLM / Ollama / OpenAI) → Grounded answer
```

The required path is **§1 → §8** — at the end you have a chat widget on
the front page answering from your content. Sections labelled *Optional*
or *Advanced* can be skipped without breaking the demo.

---

## Prerequisites

- Docker 20.10+, DDEV 1.25+, Git, curl, jq
- 8 GB RAM, 20 GB free disk (more if you load larger LLMs)
- Free ports: `9200` (Elasticsearch), `5601` (Kibana), `1234` (LM
  Studio), plus DDEV's HTTPS port
- An OpenAI-compatible AI provider running locally with **one chat
  model** *and* **one embedding model**. This guide uses
  [LM Studio](https://lmstudio.ai/) as the worked example. LiteLLM,
  Ollama, vLLM, or OpenAI direct work the same way — only the provider
  module name differs.

Install DDEV on macOS:

```bash
brew tap ddev/ddev && brew install ddev
```

Linux / Windows: see the [official DDEV install guide](https://ddev.readthedocs.io/en/stable/users/install/).
Verify everything is ready:

```bash
docker --version && ddev version && git --version && curl --version
```

---

## Section 1: Scaffold the Drupal project

```bash
git clone https://gitlab.com/ricardoamaro/labs.git
mkdir -p labs/DrupalIberia2026/drupal-ai-agent
cd labs/DrupalIberia2026/drupal-ai-agent
```

> **Critical:** always run `ddev config` from inside `drupal-ai-agent`.
> Running it from a parent directory attaches DDEV to the wrong project
> and breaks everything.

```bash
ddev config --project-type=drupal11 --docroot=web
```

If there is no `composer.json` yet (starting from scratch), scaffold a
fresh Drupal 11 project:

```bash
ddev composer create-project drupal/recommended-project:^11 . --no-interaction
```

> If the command fails because the directory is not empty, remove the
> `web/` folder first. DDEV 1.25+ scaffolds `web/sites/default/settings.php`
> during `ddev config`, so a plain `rmdir web` fails — use `rm -rf web`.

```bash
ddev start
ddev status   # Project name must show 'drupal-ai-agent'
```

`ddev start` prints the URL (e.g. `https://drupal-ai-agent.ddev.site`) —
use whatever DDEV prints anywhere this guide says "open Drupal".

---

## Section 2: Install modules

```bash
ddev composer config minimum-stability alpha
ddev composer config prefer-stable true

ddev composer require \
  'drush/drush' \
  'drupal/ai' \
  'drupal/ai_agents' \
  'drupal/modeler_api' \
  'drupal/ai_provider_lmstudio' \
  'drupal/key' \
  'drupal/search_api' \
  'drupal/search_api_attachments' \
  'drupal/ai_vdb_provider_elasticsearch:^1.0@alpha'
```

> Using a different backend? Swap `drupal/ai_provider_lmstudio` for
> `drupal/ai_provider_litellm`, `drupal/ai_provider_ollama`, or
> `drupal/ai_provider_openai`. The rest of the tutorial is identical.
>
> The `elastic/elasticsearch` PHP client is pulled in transitively — do
> not require it separately. **Never install
> `drupal/elasticsearch_connector`** — it conflicts with the Search API
> server form.

Install Drupal and enable the modules:

```bash
ddev drush site:install --account-name=admin --account-pass=admin --yes

ddev drush pm:enable \
  ai ai_search ai_provider_lmstudio key \
  modeler_api ai_agents ai_agents_explorer ai_agents_extra ai_agents_extra_tools \
  ai_chatbot ai_assistant_api ai_api_explorer \
  search_api search_api_attachments ai_vdb_provider_elasticsearch \
  --yes

ddev drush pm:list --status=enabled | grep -E "(ai|search_api|elasticsearch)"
```

---

## Section 3: Run Elasticsearch

The official `elastic-start-local` installer runs ES + Kibana in Docker
and writes credentials to `elastic-start-local/.env`:

```bash
cd ..   # from drupal-ai-agent to DrupalIberia2026
curl -fsSL https://elastic.co/start-local | sh
cd drupal-ai-agent
source ../elastic-start-local/.env
```

Kibana is at `http://localhost:5601`. Start/stop later with
`../elastic-start-local/start.sh` and `stop.sh`.

> **Linux only:** the installer binds ES to `127.0.0.1`, which DDEV
> containers cannot reach. Open it to the bridge:
>
> ```bash
> sed -i 's/127\.0\.0\.1:\${ES_LOCAL_PORT}/0.0.0.0:${ES_LOCAL_PORT}/' \
>   ../elastic-start-local/docker-compose.yml
> (cd ../elastic-start-local && docker compose down && docker compose up --wait)
> source ../elastic-start-local/.env
> ```

### 3.1 Reach Elasticsearch from inside DDEV

Drupal runs inside a DDEV container, ES runs outside. The Drupal-side
host depends on the OS:

- **macOS / Windows:** `http://host.docker.internal:9200`
- **Linux:** the Docker bridge IP — get it with:

  ```bash
  HOST_IP=$(ddev exec ip route | grep default | awk '{print $3}')
  echo "Bridge IP: $HOST_IP"   # use http://$HOST_IP:9200 in Drupal
  ```

Same rule applies to the AI provider (LM Studio, LiteLLM, Ollama)
running on your host. Verify ES is reachable from both your terminal
and from inside DDEV:

```bash
curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cluster/health?pretty"
ddev exec curl -s -u "elastic:${ES_LOCAL_PASSWORD}" \
  "http://${HOST_IP:-host.docker.internal}:9200/_cluster/health"
# Expected: "status": "green" or "yellow"
```

---

## Section 4: Configure the VDB provider

### 4.1 Store the Elasticsearch API key as a Key entity

The VDB provider's *API Key* field is a reference to a Drupal **Key**
entity, not a plain text field.

```bash
source ../elastic-start-local/.env
echo $ES_LOCAL_API_KEY
```

Open `/admin/config/system/keys/add` and create:

| Field | Value |
|---|---|
| Key name | `Elasticsearch API Key` |
| Key type | `Authentication` |
| Key value | the value of `$ES_LOCAL_API_KEY` |

### 4.2 Configure the provider

Go to `/admin/config/ai/vdb_providers/elasticsearch`:

| Field | Value |
|---|---|
| Elasticsearch Host URL | `http://host.docker.internal:9200` (Mac/Win) or `http://BRIDGE_IP:9200` (Linux) |
| API Key | `Elasticsearch API Key` (the Key entity from §4.1) |
| Basic Auth Username / Password | leave empty |
| Index Prefix | `drupal_` (required — the ES index becomes `drupal_files`) |
| Similarity Metric | `Cosine` (recommended for normalised embeddings) |
| Enable Hybrid Search | ☐ off unless you have a Platinum/Enterprise licence (see §11) |
| RRF Rank Constant | `20` (default) |

Save — confirm there is no connection error.

---

## Section 5: Configure the AI provider

This guide uses LM Studio because it is free, local, and exposes an
OpenAI-compatible API on `localhost:1234`. Substitute as needed:

| Provider | Drupal module | Default endpoint | API key? |
|---|---|---|---|
| **LM Studio** *(worked example)* | `drupal/ai_provider_lmstudio` | `http://host.docker.internal:1234` | none |
| LiteLLM | `drupal/ai_provider_litellm` | `http://host.docker.internal:4000` | yes (`LITELLM_API_KEY`) |
| Ollama | `drupal/ai_provider_ollama` | `http://host.docker.internal:11434` | none |
| OpenAI direct | `drupal/ai_provider_openai` | `https://api.openai.com` | yes (`OPENAI_API_KEY`) |

Pick **one chat model** that emits OpenAI-style function calls reliably
*and* **one embedding model**. Tested pairings:

| Provider | Chat | Embedding | Dims |
|---|---|---|---|
| LM Studio | `qwen/qwen3-32b`, `openai/gpt-oss-20b`, `google/gemma-3-27b` | `text-embedding-nomic-embed-text-v1.5` | 768 |
| LiteLLM | `gemini-flash-latest` (or any model the proxy fronts) | `text-embedding-3-small` / `gemini-embedding-001` | 1536 / 3072 |
| Ollama | `qwen2.5:32b`, `llama3.3:70b` | `nomic-embed-text:v1.5` | 768 |
| OpenAI | `gpt-4o-mini`, `gpt-5` | `text-embedding-3-small` | 1536 |

> **Tool-calling reliability matters more than parameter count.**
> Reasoning-only models (e.g. `mistralai/ministral-3-14b-reasoning`,
> OpenAI o1) burn the entire token budget on `<think>` and never emit a
> tool call — pick an instruction-tuned chat model.

### 5.1 Verify the provider exposes both models

Start your provider's server (LM Studio: Developer tab → **Start
Server**) then list models:

```bash
# LM Studio
curl -s http://localhost:1234/v1/models | jq '.data[].id'
# LiteLLM
curl -s http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_API_KEY" | jq '.data[].id'
# Ollama
curl -s http://localhost:11434/v1/models | jq '.data[].id'
```

You must see at least one chat model **and** one embedding model.

> **LM Studio + Apple Silicon:** some MLX-backend models fail to load
> via the API with `dlopen ... libpython3.11.dylib not found`. If a
> model loads in LM Studio's chat UI but breaks on the API, swap to a
> llama.cpp GGUF model or reinstall the MLX runtime from
> Settings → Runtimes.

### 5.2 Configure the provider in Drupal

Providers that need an API key (LiteLLM, OpenAI direct): create a Key
entity at `/admin/config/system/keys/add` first, then select it in the
**API Key** field on the provider page. LM Studio and Ollama need no
auth.

For LM Studio specifically, go to `/admin/config/ai/providers/lmstudio`:

| Field | Value |
|---|---|
| Host Name | `http://host.docker.internal` (Mac/Win) or the bridge IP from §3.1 (Linux) |
| Port | `1234` |

Save.

### 5.3 Set the global default models

The Search API backend, the AI Assistant, and the chatbot all read
their default provider/model from `ai.settings`:

```bash
ddev drush ev '
\Drupal::configFactory()->getEditable("ai.settings")->set("default_providers", [
  "chat"            => ["provider_id" => "lmstudio", "model_id" => "google/gemma-3-27b"],
  "embeddings"      => ["provider_id" => "lmstudio", "model_id" => "text-embedding-nomic-embed-text-v1.5"],
  "chat_with_tools" => ["provider_id" => "lmstudio", "model_id" => "qwen/qwen3-32b"],
])->save();
'
```

Substitute `provider_id` (`lmstudio`, `litellm`, `ollama`, `openai`) and
`model_id` for whatever your `/v1/models` call returned.

---

## Section 6: Configure Search API

### 6.1 Add the Elasticsearch server

Go to `/admin/config/search/search-api/add-server`:

| Field | Value |
|---|---|
| Server name | `AI Search ES` |
| Machine name | `ai_search_es` |
| Backend | `AI Search` |

Under **Configure AI Search backend**:

| Field | Value |
|---|---|
| Embeddings Engine | your provider's embedding model (e.g. `LM Studio \| text-embedding-nomic-embed-text-v1.5`) |
| Tokenizer chat counting model | your provider's chat model (e.g. `LM Studio \| google/gemma-3-27b`) |
| Vector Database | `Elasticsearch (Native kNN)` |

Under **Vector Database Configuration**:

| Field | Value |
|---|---|
| Database Name | `default` |
| Collection | `files` |
| Similarity Metric | `Cosine Similarity` |

Under **Advanced Embeddings Engine Configuration**: leave **Set
Dimensions Manually** *off* — the form auto-detects (`768` for
`nomic-embed-text-v1.5`, `1536` for `text-embedding-3-small`, `3072` for
`gemini-embedding-001`).

Under **Advanced Embeddings Strategy Configuration**:

| Field | Value |
|---|---|
| Strategy | `Enriched Embedding Strategy` |
| Maximum chunk size | `500` tokens |
| Minimum chunk overlap for Main Content | `100` tokens |

> **Both chunk values are required, not optional.** If you script the
> server creation and forget them, the indexer fatals with
> `Typed property ...EmbeddingBase::$chunkMinOverlap must not be
> accessed before initialization`. The form's defaults (`500` / `100`)
> are sane — leave them alone.

Save and confirm the green "server could be reached" message.

### 6.2 Create the index

Go to `/admin/config/search/search-api/add-index`:

| Field | Value |
|---|---|
| Index name | `Files` |
| Machine name | `files` *(with prefix `drupal_` the ES index becomes `drupal_files`)* |
| Datasources | ✅ `Content` (Article bundle only) **and** ✅ `File` |
| Server | `AI Search ES` |

> **Why two datasources?** Article bodies live on the node entity;
> extracted PDF / Markdown text lives on the file entity. With both,
> the same index serves editorial pages and document uploads, and the
> chatbot retrieves either path identically.

### 6.3 Add fields and mark them for AI Search

Go to `/admin/config/search/search-api/index/files/fields` →
**Add fields**. Each row also has an **AI Search indexing option**
column — set both at the same time:

| Field machine name | Label | Datasource | Type | AI Search indexing option |
|---|---|---|---|---|
| `node_title` | Article: Title | Content | String | **Contextual content** |
| `node_body` | Article: Body | Content | Fulltext | **Main content** |
| `filename` | Filename | File | String | **Contextual content** |
| `saa_saa_file_entity` *(processor-added — see §6.4)* | Extracted text | *(none)* | Fulltext | **Main content** |

The form will not save until every field has an indexing option set —
**Ignore** is a valid choice if you want to exclude a field. Save; the
ES index is created on the first index run.

> **Why prefix node fields with `node_`?** Both datasources expose a
> "Title" property; prefixing the node side disambiguates which
> datasource each field comes from.

### 6.4 Configure the PDF extractor and the file-attachments processor

**A.** At `/admin/config/search/search-api-attachments`:

| Field | Value |
|---|---|
| Extraction method | `PHP PdfParser (smalot/pdfparser)` |
| Read text files directly | ✅ on (lets `.md`, `.txt`, `.rst`, `.org`, `.adoc`, `.log`, `.yaml`, `.ini`, `.toml` flow through without an extractor) |

**B.** At `/admin/config/search/search-api/index/files/processors`,
tick **File attachments** and save. This processor materialises the
`saa_saa_file_entity` field listed in §6.3.

> **Datasource = (none) is intentional** for the processor-added field.
> Setting `setDatasourceId("entity:file")` from drush silently drops the
> field on `addField()`.

### 6.5 (Optional) drush equivalent

Prefer one shell snippet over form clicking? See
[`01-bootstrap-pdf-upload.md`](01-bootstrap-pdf-upload.md) Steps 8–9 —
it bootstraps the same server, index, fields, and processor in a single
`ddev drush ev` block. Match the dimensions to your embedding model
(`768` / `1536` / `3072`).

---

## Section 7: Add content and index

### 7.1 Create at least one Article

Go to `/node/add/article` and create one or two articles with rich body
text — longer, more descriptive content yields better embeddings.
Suggested topic: a "GDPR Compliance Guide" covering data subject
rights, 72-hour breach reporting, and lawful basis (lets you test
semantic search against the prompt *"data privacy in Europe"* later
without keyword overlap).

### 7.2 Reset and index

```bash
ddev drush search-api:reset-tracker files
ddev drush search-api:index files
```

> Run a full reset whenever you change the AI Search backend settings,
> the embedding model, the field list, the AI Search field mapping, or
> add a new extractor. Routine content saves index automatically
> (Search API's `index_directly` is on by default).

### 7.3 Verify the index and the vectors

```bash
source ../elastic-start-local/.env
curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cat/indices?v" | grep drupal
# Expect: drupal_files

# Generate an embedding from the provider, run a kNN query against ES.
EMB=$(curl -s http://localhost:1234/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-nomic-embed-text-v1.5","input":"GDPR compliance"}' | \
  python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['data'][0]['embedding']))")

curl -s -u "elastic:${ES_LOCAL_PASSWORD}" \
  "http://localhost:9200/drupal_files/_search" \
  -H "Content-Type: application/json" \
  -d "{\"knn\":{\"field\":\"vector\",\"query_vector\":${EMB},\"k\":3,\"num_candidates\":50},\"size\":3}" | \
  python3 -c "
import json,sys
hits=json.load(sys.stdin).get('hits',{}).get('hits',[])
print(len(hits),'results')
for h in hits: print(' -', h['_source'].get('content','')[:80])
"
```

> **ES 9.x note:** `_source` will not contain a `vector` key — ES 9.x
> stores `dense_vector` fields in compressed BBQ form for kNN, not in
> `_source`. The kNN query above returning hits is the correct proof
> that vectors are indexed.

---

## Section 8: Frontend chatbot (the demo widget)

This is the surface to use during the workshop demo. It uses the
`ai_assistant_api` + `ai_chatbot` submodules (both already enabled in
§2). The path is:

```
chat widget → /api/deepchat → AI Assistant entity → rag_action
  → Search API 'files' index → Elasticsearch kNN → LLM
```

> **Two RAG plugin systems coexist.** `ai_agents` (§9, optional) calls
> RAG through `RagTool` (a `FunctionCall`). `ai_assistant_api` (used
> here) calls RAG through `RagAction` (an `AiAssistantAction`). Same
> logic, different plugin registrations — that is why the chatbot's
> *Assistant* entity is separate from §9's *Agent* entity.

### 8.1 Create the AI Assistant entity

```bash
ddev drush ev '
use Drupal\ai_assistant_api\Entity\AiAssistant;
$default = \Drupal::config("ai.settings")->get("default_providers")["chat_with_tools"];
if ($a = AiAssistant::load("docs_chatbot")) { $a->delete(); }
AiAssistant::create([
  "id" => "docs_chatbot",
  "label" => "Docs Chatbot",
  "description" => "Frontend chatbot that runs RAG against the indexed Drupal articles + uploaded documents.",
  "system_prompt" => "You are a helpful assistant that answers questions about the user`s documents and articles. Use only the information returned by the RAG action. Quote titles and summarise relevant passages. If the documents do not contain the answer, say so plainly.",
  "allow_history" => "session",
  "history_context_length" => "4",
  "assistant_message" => "Hi — I`m your content assistant. Ask me anything about the indexed articles and documents.",
  "no_results_message" => "I couldn`t find anything in the indexed content that answers that.",
  "error_message" => "Sorry, something went wrong. Please try again.",
  "llm_provider" => $default["provider_id"],
  "llm_model" => $default["model_id"],
  "actions_enabled" => [
    "rag_action" => [
      "rag_0" => [
        "database" => "files",
        "description" => "Indexed PDFs and Article bodies",
        "score_threshold" => "0",
        "min_results" => "1",
        "max_results" => "5",
        "output_mode" => "chunks",
        "rendered_view_mode" => "full",
        "aggregated_llm" => "",
        "access_check" => FALSE,
        "try_reuse" => FALSE,
        "context_threshold" => "0",
      ],
    ],
  ],
  "use_function_calling" => FALSE,
])->save();
echo "Created docs_chatbot using {$default["provider_id"]} / {$default["model_id"]}\n";
'
```

> **⚠️ `actions_enabled` shape is fragile.** It must be
> `[plugin_id => [instance_id => inner_config]]`. A flat config (no
> instance key) or `[plugin_id => [enabled, plugin_id, configuration]]`
> both crash `RagAction::searchRagAction()` with
> `TypeError: Cannot access offset of type string on string`. The
> `instance_id` (`rag_0` here) is arbitrary — one assistant can target
> multiple Search API indexes by adding more instance keys.

The UI form at `/admin/config/ai/ai-assistant/add` persists the same
shape correctly if you prefer clicks.

### 8.2 Place the chatbot block

```bash
ddev drush ev '
use Drupal\block\Entity\Block;
$theme = \Drupal::config("system.theme")->get("default");
if ($b = Block::load("ai_chatbot_docs")) { $b->delete(); }
Block::create([
  "id" => "ai_chatbot_docs",
  "theme" => $theme,
  "region" => "content",
  "weight" => -10,
  "plugin" => "ai_chatbot_block",
  "settings" => [
    "id" => "ai_chatbot_block",
    "label" => "Docs Chatbot",
    "label_display" => "visible",
    "provider" => "ai_chatbot",
    "ai_assistant" => "docs_chatbot",
    "verbose_mode" => FALSE,
    "stream" => FALSE,
    "first_message" => "Hi — ask me anything about the indexed Drupal content.",
    "toggle_collapse" => FALSE,
    "use_thread_history" => FALSE,
    "questions_per_thread" => "0",
  ],
  "visibility" => [
    "request_path" => ["id" => "request_path", "pages" => "<front>", "negate" => FALSE],
  ],
])->save();
echo "Block placed in $theme/content, visible on <front>\n";
'
```

> **`ai_chatbot_block` vs `ai_deepchat_block`:** both ship in the same
> module. `ai_chatbot_block` (used here) is the simple inline form —
> what `02-ai-chatbot-setup.md` validates. `ai_deepchat_block` is the
> richer floating-bubble widget. Keep the inline one for the demo.

To put the chatbot on every page, drop the `visibility` array; for a
specific page, change `pages` to e.g. `/node/1`.

### 8.3 Grant the chat permission

```bash
ddev drush ev '
foreach (["anonymous", "authenticated"] as $rid) {
  \Drupal\user\Entity\Role::load($rid)->grantPermission("access deepchat api")->save();
}
'
```

### 8.4 CLI smoke test (no browser needed)

The widget submits messages via JS → `/api/deepchat` →
`AiAssistantApiRunner::process()` → RAG action → LLM. Save this as
`run_chat.php` next to the project root and exercise the same plumbing
from drush:

```php
<?php

use Drupal\ai\OperationType\Chat\ChatInput;
use Drupal\ai\OperationType\Chat\ChatMessage;

$question = $extra[0] ?? "What articles do we have about GDPR compliance?";

$assistant = \Drupal\ai_assistant_api\Entity\AiAssistant::load('docs_chatbot');
$rag_config = $assistant->get('actions_enabled')['rag_action'];
$first_db = reset($rag_config)['database'];

$rag = \Drupal::service('ai_assistant_api.action_plugin.manager')
  ->createInstance('rag_action', $rag_config);
$rag->setAssistant($assistant);
$rag->setThreadId('cli_demo_thread');
$rag->triggerAction('search_rag', ['database' => $first_db, 'query' => $question]);

$session = $rag->getTempStore()->get('cli_demo_thread') ?? [];
$rag_chunks = $session['output_contexts']['rag'] ?? [];
$rag_context = implode("\n\n---\n\n", $rag_chunks) ?: '(empty)';

$provider = \Drupal::service('ai.provider')->createInstance($assistant->get('llm_provider'));
echo $provider->chat(new ChatInput([
  new ChatMessage('system', $assistant->get('system_prompt')),
  new ChatMessage('user', "Context:\n{$rag_context}\n\nQuestion: {$question}"),
]), $assistant->get('llm_model'))->getNormalized()->getText() . "\n";
```

```bash
ddev drush php:script run_chat.php -- "What articles do we have about GDPR compliance?"
```

A grounded answer that quotes content only present in the indexed
articles proves `RAG → kNN → LLM` is wired end-to-end.

### 8.5 Open the browser

Visit `https://drupal-ai-agent.ddev.site/`. The widget appears as a
**floating panel anchored to the bottom-right** of the viewport
(`position: fixed`) — the `content` region only controls *which pages
render the script*; the `ai_chatbot` module's CSS pins the visible
position. The "Docs Chatbot" heading at the top of the page is the
regular block label; the actual chat is the floating panel.

> **If the floating panel is missing:**
> - The Drupal **admin toolbar** sits at `bottom: 0` and covers it.
>   Log out (or open in incognito) — the chat works for anonymous
>   because §8.3 granted `access deepchat api`.
> - Sanity-check the markup:
>   `curl -s https://drupal-ai-agent.ddev.site/ | grep -oE "ai-chatbot|Docs Chatbot|live-chat" | sort -u`
>   should print all three.
> - Sanity-check the back-end with §8.4's `run_chat.php`. If that
>   answers, the UI is the only thing left to debug.

This is the surface to use for the live demo.

---

## Section 9 *(Optional)*: AI Agent Explorer for admins

The **AI Agent Explorer** is a developer/admin debugging surface that
shows tool calls, retrieved chunks, and model responses inline. It
talks to a separate `ai_agents` Agent entity (not the Assistant from
§8). Useful for showing the audience *what the chatbot is doing under
the hood*; not required for the demo itself.

### 9.1 Create the agent

```bash
ddev drush ev '
$agent = \Drupal\ai_agents\Entity\AiAgent::create([
  "id" => "content_assistant",
  "label" => "Content Assistant",
  "description" => "Answers questions about indexed Drupal content using RAG over Elasticsearch.",
  "system_prompt" => "You are a helpful Drupal content assistant. To answer ANY question, you MUST call ai_search_rag_search with relevant keywords from the question. Report titles and summarize content from all results found. If the search returns nothing relevant, say so clearly rather than guessing.",
  "secured_system_prompt" => "[ai_agent:agent_instructions]",
  "tools" => ["ai_search:rag_search" => TRUE],
  "tool_settings" => [
    "ai_search:rag_search" => [
      "return_directly" => 1,
      "require_usage" => 1,
      "use_artifacts" => 0,
    ],
  ],
  "tool_usage_limits" => [
    "ai_search:rag_search" => [
      "index" => ["action" => "force_value", "hide_property" => 1, "values" => ["files"]],
    ],
  ],
  "max_loops" => 5,
]);
$agent->save();
echo "Created agent: " . $agent->id() . PHP_EOL;
'
```

The same agent can be created at `/admin/config/ai/agents/add`. Key
settings on the *RAG/Vector Search* tool:

- ✅ **Return directly** (without it, the LLM loops through searches and gives up)
- ✅ **Require Usage** (forces the tool to always be called)
- ☐ **Use Artifact storage** *off* (artefact tokens `{{artifact:...}}` break the output)
- **Property restrictions → index** = force value `files` (Search API
  index machine name, **not** the ES index name `drupal_files`)

### 9.2 Test in the Explorer

Go to `/admin/config/ai/agents/explore`, select **Content Assistant**,
pick the `chat_with_tools` model.

| Test | Question | What it proves |
|---|---|---|
| Keyword | *"What articles do we have about GDPR compliance?"* | RAG tool wiring |
| Semantic | *"What do we have about data privacy for people in Europe?"* | kNN works without keyword overlap |

The Progress panel should show the `ai_search_rag_search` tool call
and the retrieved chunks. CLI alternative:

```bash
ddev drush ev '
use Drupal\ai_agents\Task\Task;
$default = \Drupal::config("ai.settings")->get("default_providers")["chat_with_tools"];
$provider = \Drupal::service("ai.provider")->createInstance($default["provider_id"]);
$agent = \Drupal::service("plugin.manager.ai_agents")->createInstance("content_assistant");
$agent->setTask(new Task("What articles do we have about GDPR compliance?"));
$agent->setAiProvider($provider);
$agent->setModelName($default["model_id"]);
$agent->setAiConfiguration([]);
$agent->setCreateDirectly(TRUE);
echo $agent->solve();
'
```

---

## Section 10 *(Optional)*: Index PDFs and other documents

The `ai_vdb_provider_elasticsearch` module ships its own pure-PHP PDF
extractor (`smalot/pdfparser`) and registers `text/*` MIME types for
`.md`, `.rst`, `.org`, `.adoc`, `.log`, `.yaml`, `.ini`, `.toml`. **No
system binaries required.** Wire-up was already done in §6.4 — this
section just authors content.

### 10.1 Allow document extensions on the Document media type

Edit `field_media_document` at
`/admin/structure/media/manage/document` → **Manage fields** →
*Document file* → **Allowed file extensions**:

```
pdf md markdown rst org adoc asciidoc txt log yaml yml ini conf toml
```

> Don't create a *second* Document media type — the form ends up with
> two file widgets and submission fails with *"Document field is
> required"*.

### 10.2 Generate a sample PDF (no Word / Pages needed)

The DDEV web container ships `ps2pdf`:

```bash
ddev exec bash -c "cat > /tmp/sample.ps <<'PS'
%!PS
/Helvetica findfont 12 scalefont setfont
72 720 moveto (Elasticsearch is a distributed search engine.) show
72 700 moveto (This document tests the Drupal AI VDB Provider for Elasticsearch.) show
72 680 moveto (Topics: vector search, kNN, dense_vector, HNSW, RAG.) show
showpage
PS
cp /tmp/sample.ps /var/www/html/sample.ps && ps2pdf /var/www/html/sample.ps /var/www/html/sample.pdf"

ddev drush ev '
$file = \Drupal::service("file.repository")->writeData(
  file_get_contents("/var/www/html/sample.pdf"),
  "public://sample.pdf",
  \Drupal\Core\File\FileExists::Replace,
);
$file->setPermanent(); $file->save();
echo "fid=" . $file->id() . PHP_EOL;
'
```

For the workshop, also upload via `/media/add/document` (Content →
Media → Add media → Document).

### 10.3 Verify

```bash
ddev drush search-api:status files     # tracks % indexed
ddev drush search-api:index files      # flush the queue manually if needed

source ../elastic-start-local/.env
curl -s -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/drupal_files/_count"
```

PDFs (extracted via `smalot/pdfparser`) and Markdown files (read
directly because their MIME is `text/markdown`) end up in the same
index alongside Article bodies — the chatbot retrieves them
identically.

---

## Section 11 *(Advanced)*: Hybrid search with RRF

`ai_vdb_provider_elasticsearch` is the only Drupal VDB provider with
built-in hybrid search — it combines kNN + BM25 in a single ES query
using **Reciprocal Rank Fusion**, no manual score normalisation needed.

Toggle it on in **§4.2 → Enable Hybrid Search**. **Requires Elastic
Platinum/Enterprise** (RRF is a paid feature). Free/basic clusters
return `403 license non-compliant for [Reciprocal Rank Fusion (RRF)]` —
either start a Platinum trial in
*Kibana → Stack Management → License Management → Start trial* or
leave hybrid off and rely on pure kNN.

### Optional: Boost-by-AI-Search processor

`ai_search` ships a **Boost Database by AI Search** processor that
blends kNN results into standard Search API queries. Enable it on the
index at `/admin/config/search/search-api/index/files/processors`,
configure index = `files`, weight between `0.1` (subtle) and `1.0`
(strong semantic preference), then reindex.

---

## Section 12 *(Advanced)*: Multi-agent swarm orchestration

Use a coordinator + grader pattern to filter low-quality chunks before
the LLM answers.

1. On the §9 *Content Assistant* form, tick **Swarm orchestration agent**.
2. Create a second agent `relevance_grader` with the lightest chat
   model you have. Instructions:
   ```
   You are a document relevance grader. You receive a question and a
   retrieved document chunk. Respond with only "RELEVANT" if the
   document genuinely helps answer the question, or "IRRELEVANT" if it
   does not. Do not add explanation. One word only.
   ```
   No tools.
3. Back on the *Content Assistant*, add **Relevance Grader** as a
   sub-agent tool alongside RAG/Vector Search and update the
   instructions:
   ```
   You are a Drupal content assistant coordinating a two-stage retrieval workflow.
   Step 1: Call ai_search_rag_search to retrieve candidate documents.
   Step 2: For each retrieved chunk, call relevance_grader (RELEVANT / IRRELEVANT).
   Step 3: Use only RELEVANT chunks for the final answer.
   ```

The Explorer's Progress panel now shows two agent steps — retrieval
and grading.

---

## Section 13 *(Advanced)*: Browse the index with Kibana

Kibana runs as part of `elastic-start-local`. Open
`http://localhost:5601` and log in as `elastic` with `ES_LOCAL_PASSWORD`
from `../elastic-start-local/.env`.

**Data View** at `http://localhost:5601/app/management/kibana/dataViews`:
name `Drupal Files`, index pattern `drupal_files`, no timestamp.

**Discover** at `http://localhost:5601/app/discover` — pick *Drupal
Files*; expand any row to see `content`, `drupal_entity_id`, `index_id`,
`server_id`. Reminder: `vector` is **not** in `_source` on ES 9.x — it
is stored in compressed BBQ form for kNN.

**Dev Tools** at `http://localhost:5601/app/dev_tools#/console`:

```json
GET drupal_files/_search
{"query": {"match": {"content": "GDPR compliance"}}}

GET drupal_files/_mapping
// Look for: "vector": {"type":"dense_vector","dims":768,"index_options":{"type":"bbq_hnsw"}}
```

The hybrid query the module sends internally (when enabled) uses an
`rrf` retriever with a kNN leg pre-computed by the AI provider — you
cannot replicate it from Dev Tools without a hand-built query vector.

---

## Section 14 *(Optional)*: MCP server integration

Lets external assistants (Claude, Cursor) talk to Drupal via the
Model Context Protocol.

> The Composer release has a broken dependency. Clone the module
> directly:

```bash
ddev composer require \
  'drupal/simple_oauth:^6' \
  'e0ipso/simple_oauth_21:^1@dev' \
  'drupal/tool:^1.0@alpha' \
  'mcp/sdk:^0.4'

git clone https://git.drupalcode.org/project/mcp_server.git \
  web/modules/contrib/mcp_server

ddev drush pm:enable mcp_server --yes
npx @modelcontextprotocol/inspector ddev drush mcp:stdio
```

---

## Troubleshooting

**Cannot reach Elasticsearch from inside DDEV.**
The host must be `http://host.docker.internal:9200` (Mac/Win) or
`http://BRIDGE_IP:9200` (Linux), never `localhost:9200`. On Linux the
installer binds ES to `127.0.0.1` — see the §3 fix and
`HOST_IP=$(ddev exec ip route | grep default | awk '{print $3}')`.

**ES index not created after indexing.**
Confirm Vector Database, Embeddings Engine, and Tokenizer chat counting
model are all set on the server, then full reset:

```bash
ddev drush search-api:reset-tracker files && ddev drush search-api:index files
source ../elastic-start-local/.env
curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cat/indices?v"
```

**Vector dimension mismatch on indexing.**
You changed embedding models without dropping the ES index. Delete and
reindex:
```bash
source ../elastic-start-local/.env
curl -u "elastic:${ES_LOCAL_PASSWORD}" -X DELETE "http://localhost:9200/drupal_files"
ddev drush search-api:reset-tracker files && ddev drush search-api:index files
```

**PDF / Markdown uploaded but never returned by RAG.**
- Ensure the **File attachments** processor is enabled on the index
  (§6.4-B) and the `saa_saa_file_entity` field's AI Search indexing
  option is **Main content** (§6.3).
- For Markdown / RST etc. uploads coming back as
  `application/octet-stream`, **Read text files directly** must be on
  at `/admin/config/search/search-api-attachments`.
- Re-run the reset+index combo above.

**Hybrid search returns 403 (RRF licence).**
Either start a Platinum trial (Kibana → Stack Management → License
Management) or disable hybrid:
```bash
ddev drush ev '\Drupal::configFactory()
  ->getEditable("ai_vdb_provider_elasticsearch.settings")
  ->set("hybrid_search", FALSE)->save();'
```

**Agent uses the wrong index name.**
The RAG tool's `index` parameter is the **Search API** index machine
name (`files`), not the ES index name (`drupal_files`). Repair from
drush:
```bash
ddev drush ev '
$agent = \Drupal\ai_agents\Entity\AiAgent::load("content_assistant");
$limits = $agent->get("tool_usage_limits") ?? [];
$limits["ai_search:rag_search"] = ["index" => ["action" => "force_value", "hide_property" => 1, "values" => ["files"]]];
$agent->set("tool_usage_limits", $limits)->save();
'
```

**Agent returns `{{artifact:ai_search_rag_search:1}}`** — uncheck **Use
Artifact storage** on the RAG tool.

**Agent loops then says "Not Solvable"** — re-enable **Return
directly** on the RAG tool.

**Chatbot crashes with `TypeError: Cannot access offset of type string
on string` in `RagAction`.** `actions_enabled` shape is wrong — it must
be `[plugin_id => [instance_id => inner_config]]` (see §8.1).

**No embedding model in your AI provider.**
List what is loaded and grep for embedders:
```bash
curl -s http://localhost:1234/v1/models | jq '.data[].id' | grep -i embed   # LM Studio
curl -s http://localhost:11434/v1/models | jq '.data[].id' | grep -i embed  # Ollama
```
If empty, load one from your provider's UI (LM Studio's *Discover*,
`ollama pull nomic-embed-text`, or update `litellm.config.yaml`).

**Drupal logs `Could not load the <provider> API key`.** Harmless when
the provider doesn't need auth (LM Studio, Ollama). To silence it,
create a dummy Key entity at `/admin/config/system/keys/add` with value
`none` and reference it from the provider config.

**Chat returns `reasoning_content` but empty `content` /
`finish_reason=length`.** You picked a reasoning-trained model that
burned the budget on `<think>`. Pick `qwen/qwen3-32b`, `gpt-oss-20b`,
`google/gemma-3-27b`, or `gpt-4o-mini` for tool-calling — or raise
`max_tokens`.

**Composer stability errors.**
```bash
ddev composer config minimum-stability alpha && ddev composer config prefer-stable true
```

**DDEV project named incorrectly.** Run from inside `drupal-ai-agent`:
```bash
ddev stop && ddev config --project-type=drupal11 --docroot=web && ddev start
```

---

## Quick reference

| Task | Command |
|---|---|
| Start ES + Kibana | `../elastic-start-local/start.sh` |
| Stop ES + Kibana | `../elastic-start-local/stop.sh` |
| Start / stop / restart DDEV | `ddev start` / `ddev stop` / `ddev restart` |
| Index status | `ddev drush search-api:status files` |
| Full reset + reindex | `ddev drush search-api:reset-tracker files && ddev drush search-api:index files` |
| List ES indices | `source ../elastic-start-local/.env && curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cat/indices?v" \| grep drupal` |
| ES health | `source ../elastic-start-local/.env && curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cluster/health?pretty"` |
| Open Agent Explorer | `ddev launch /admin/config/ai/agents/explore` |
| Bridge IP (Linux) | `ddev exec ip route \| grep default \| awk '{print $3}'` |
| Rebuild cache | `ddev drush cr` |

---

## Demo cheat sheet

ES password and API key shown as `${ES_LOCAL_PASSWORD}` and
`${ES_LOCAL_API_KEY}` are unique to your install — find them in
`elastic-start-local/.env` (`source ../elastic-start-local/.env` from
inside `drupal-ai-agent` exposes them as shell variables).

### URLs

| Service | URL | Auth |
|---|---|---|
| **Frontend chatbot** *(main demo surface, §8)* | https://drupal-ai-agent.ddev.site/ | none for visitors; `access deepchat api` permission |
| Drupal admin | https://drupal-ai-agent.ddev.site/admin | `admin` / `admin` |
| AI Agent Explorer *(§9, debugging)* | https://drupal-ai-agent.ddev.site/admin/config/ai/agents/explore | (same login) |
| AI Assistant config | https://drupal-ai-agent.ddev.site/admin/config/ai/ai-assistant | (same login) |
| Block layout | https://drupal-ai-agent.ddev.site/admin/structure/block | (same login) |
| AI Providers | https://drupal-ai-agent.ddev.site/admin/config/ai/providers | (same login) |
| VDB Provider config | https://drupal-ai-agent.ddev.site/admin/config/ai/vdb_providers/elasticsearch | (same login) |
| Search API | https://drupal-ai-agent.ddev.site/admin/config/search/search-api | (same login) |
| Drupal Keys | https://drupal-ai-agent.ddev.site/admin/config/system/keys | (same login) |
| **Kibana** | http://localhost:5601 | `elastic` / `${ES_LOCAL_PASSWORD}` |
| Kibana Discover | http://localhost:5601/app/discover | (same login) |
| Kibana Dev Tools | http://localhost:5601/app/dev_tools#/console | (same login) |
| **Elasticsearch API** | http://localhost:9200 | `elastic` / `${ES_LOCAL_PASSWORD}` or `Authorization: ApiKey ${ES_LOCAL_API_KEY}` |
| **LM Studio API** | http://localhost:1234 | none |

> **Linux:** replace `host.docker.internal` with the bridge IP from §3.1
> when configuring services in Drupal (`localhost` works from your
> terminal, not from inside DDEV).

### Live-check commands

```bash
cd /path/to/labs/DrupalIberia2026/drupal-ai-agent
source ../elastic-start-local/.env

# 1. ES is alive and green/yellow
curl -s -u "elastic:${ES_LOCAL_PASSWORD}" http://localhost:9200/_cluster/health?pretty

# 2. drupal_files has documents
curl -s -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/drupal_files/_count"

# 3. LM Studio exposes both chat and embedding models
curl -s http://localhost:1234/v1/models | jq '.data[].id'

# 4. The chatbot answers from indexed content (CLI fallback for the UI)
ddev drush php:script run_chat.php -- "What articles do we have about GDPR compliance?"
```

### Suggested 5-minute demo path

1. **Show ES is live and indexed** —
   `curl .../_cluster/health` → green, `curl .../drupal_files/_count` → non-zero.
2. **Show the data in Kibana Discover** — open *Drupal Files*, expand
   a row, point out `content` / `entity_id` / `chunk_id`. Mention that
   `vector` is *not* in `_source` on ES 9.x (BBQ-quantised storage).
3. **Show the model is local** — switch to LM Studio, show the loaded
   chat + embedding models. No cloud, no API key.
4. **Open the public site** — visit `/`, audience sees the *Docs
   Chatbot* widget. Ask:
   - *"What articles do we have about GDPR compliance?"* (keyword)
   - *"What do we have about data privacy for people in Europe?"*
     (semantic — no keyword overlap, GDPR article still wins)
5. *(Optional)* Open the **Agent Explorer** and ask the same question
   to expose the `ai_search_rag_search` tool call and retrieved chunks.

> **⚠️ Don't paste the literal `ES_LOCAL_PASSWORD` /
> `ES_LOCAL_API_KEY` values into any committed file, screen recording,
> slide deck, or chat log.** They're development credentials but treat
> them like real ones; `elastic-start-local` regenerates them on every
> fresh install. The project's `.gitignore` already excludes
> `elastic-start-local/`.

---

## Cleanup

```bash
ddev stop && ddev delete -O -y
docker volume prune -f && docker image prune -f
```

---

## Resources

- [Drupal AI Module](https://www.drupal.org/project/ai)
- [AI VDB Provider Elasticsearch](https://www.drupal.org/project/ai_vdb_provider_elasticsearch)
- [Elasticsearch dense_vector](https://www.elastic.co/guide/en/elasticsearch/reference/current/dense-vector.html)
- [Elasticsearch kNN search](https://www.elastic.co/guide/en/elasticsearch/reference/current/knn-search.html)
- [DDEV documentation](https://ddev.readthedocs.io/)
- [LM Studio](https://lmstudio.ai/)
- [LiteLLM documentation](https://docs.litellm.ai/)
- [MCP Server module](https://www.drupal.org/project/mcp_server)
- Reference recipes used to validate this guide:
  [01-bootstrap-pdf-upload.md](01-bootstrap-pdf-upload.md),
  [02-ai-chatbot-setup.md](02-ai-chatbot-setup.md)
