# Drupal AI Agent with Elasticsearch

Build a RAG-powered AI agent inside Drupal that answers questions using your content, indexed in Elasticsearch with kNN dense vector semantic search, answered by an LLM.

```
User question → Drupal AI Agent → RAG search (AI Search VDB)
  → Elasticsearch kNN dense vectors → LiteLLM → Grounded answer
```

## Prerequisites

- Docker 20.10+, DDEV (latest), Git, curl, jq
- An OpenAI-compatible AI provider running locally with **one chat model and one embedding model**. This tutorial uses [LM Studio](https://lmstudio.ai/) (default port `1234`), but LiteLLM, Ollama, vLLM, or any OpenAI-compatible endpoint works
- 8GB RAM, 20GB free disk (more if you load larger LLMs into LM Studio)
- Ports 9200 (Elasticsearch), 33000 (DDEV), 1234 (LM Studio) free

### Install DDEV (macOS)

```bash
brew tap ddev/ddev
brew install ddev
```

For Linux / Windows, follow the [official DDEV install guide](https://ddev.readthedocs.io/en/stable/users/install/).

Verify all prerequisites:

```bash
docker --version && ddev version && git --version && curl --version
```

### LM Studio models used in this tutorial

Open LM Studio → **Models** and load at least one chat model and one embedding model. Recommended:

| Role | Model | Why |
|---|---|---|
| Chat / agent | `qwen/qwen3-32b` (or `qwen3.6-35b-a3b` MoE if you have it) | Strong tool-calling, OpenAI-compatible function calls work reliably |
| Chat (lighter) | `openai/gpt-oss-20b` or `google/gemma-3-27b` | Fits in less VRAM if 30B+ is too heavy |
| Embeddings | `text-embedding-nomic-embed-text-v1.5` (768 dims) | Battle-tested, fast, good multilingual coverage |

Start the LM Studio server (Developer tab → **Start Server**) and verify it returns models:

```bash
curl -s http://localhost:1234/v1/models | jq '.data[].id'
```

You should see your loaded chat and embedding models in the list.

---

## Section 1: Clone and Start DDEV

```bash
git clone https://gitlab.com/ricardoamaro/labs.git
mkdir -p labs/DrupalIberia2026/drupal-ai-agent
cd labs/DrupalIberia2026/drupal-ai-agent
```

> **Critical:** Always run `ddev config` from inside `drupal-ai-agent`. Running it from a parent directory attaches DDEV to the wrong project and breaks everything.

```bash
ddev config --project-type=drupal11 --docroot=web
```

### 1.1 Create a fresh Drupal 11 project

If there is no `composer.json` in the directory (starting from scratch), scaffold a new Drupal 11 project before starting DDEV:

```bash
ddev composer create-project drupal/recommended-project:^11 . --no-interaction
```

> If the command fails because the directory is not empty, remove the `web/` folder first. DDEV 1.25+ scaffolds a `web/sites/default/settings.php` during `ddev config`, so a plain `rmdir web` will fail — use `rm -rf web` instead.

```bash
ddev start
ddev status   # Project name must show 'drupal-ai-agent'
```

DDEV will print the URL the project is reachable at — typically `http://drupal-ai-agent.ddev.site` (modern DDEV uses standard 80/443 via its mkcert-issued certificate; no `:33000` suffix). Use whatever URL `ddev start` prints whenever this guide refers to opening Drupal in a browser.

---

## Section 2: Install Drupal and Modules

### 2.1 Set minimum stability

```bash
ddev composer config minimum-stability alpha
ddev composer config prefer-stable true
```

### 2.2 Install modules

```bash
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

> Using a different OpenAI-compatible backend? Swap `drupal/ai_provider_lmstudio` for `drupal/ai_provider_litellm`, `drupal/ai_provider_ollama`, or `drupal/ai_provider_openai` — all of the rest of this tutorial is identical, only the provider name changes.

> The `elastic/elasticsearch` PHP client ^8.0 is pulled in automatically — no need to require it separately.

> **Never install `drupal/elasticsearch_connector`** — it conflicts with the Search API server form and causes a `PluginException`.

### 2.3 Install Drupal and enable modules

```bash
ddev drush site:install --account-name=admin --account-pass=admin --yes

ddev drush pm:enable \
  ai ai_search ai_provider_lmstudio key \
  modeler_api ai_agents ai_agents_explorer ai_agents_extra ai_agents_extra_tools \
  ai_chatbot ai_assistant_api ai_api_explorer \
  search_api search_api_attachments ai_vdb_provider_elasticsearch \
  --yes
```

Verify:

```bash
ddev drush pm:list --status=enabled | grep -E "(ai|search_api|elasticsearch)"
```

---

## Section 3: Elasticsearch Setup

Run the official Elastic installer from the `DrupalIberia2026` directory. It creates the `elastic-start-local` folder, generates random passwords and an API key, and starts Elasticsearch and Kibana in Docker:

```bash
cd ..   # from drupal-ai-agent to DrupalIberia2026
curl -fsSL https://elastic.co/start-local | sh
cd drupal-ai-agent
source ../elastic-start-local/.env
```

The installer prints the password and API key on screen and saves everything to `elastic-start-local/.env`. Kibana is available at `http://localhost:5601`.

> **Linux only:** The installer binds Elasticsearch to `127.0.0.1`, which prevents DDEV containers from reaching it via the bridge IP. Fix this before proceeding:

```bash
sed -i 's/127\.0\.0\.1:\${ES_LOCAL_PORT}/0.0.0.0:${ES_LOCAL_PORT}/' ../elastic-start-local/docker-compose.yml
(cd ../elastic-start-local && docker compose down && docker compose up --wait)
source ../elastic-start-local/.env
```

Verify Elasticsearch is running and reachable from inside DDEV:

```bash
curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cluster/health?pretty"
# Expected: "status": "green" or "yellow"

HOST_IP=$(ddev exec ip route | grep default | awk '{print $3}')
ddev exec curl -s -u "elastic:${ES_LOCAL_PASSWORD}" "http://${HOST_IP}:9200/_cluster/health"
# Expected: {"status":"green"...}
```

To start/stop after the initial install:

```bash
../elastic-start-local/start.sh
../elastic-start-local/stop.sh
```

### 3.1 Get the Docker bridge IP (Linux — needed for DDEV to reach ES and LiteLLM)

Elasticsearch and LiteLLM both run outside the DDEV Docker network. On Linux, `host.docker.internal` does not work automatically. Get the IP DDEV containers use to reach the host:

```bash
HOST_IP=$(ddev exec ip route | grep default | awk '{print $3}')
echo "Bridge IP: $HOST_IP"
```

> **macOS/Windows:** Use `host.docker.internal` instead of the bridge IP for both Elasticsearch and LiteLLM.

> On Linux, you must use `http://$HOST_IP:9200` (not `localhost:9200`) when configuring the VDB provider inside Drupal, because Drupal runs inside a DDEV container.

---

## Section 4: Configure the VDB Provider

### 4.1 Store the Elasticsearch API key

The API Key field in the VDB provider is a reference to a Drupal Key entity — not a plain text field. Create one first.

Get the key value:

```bash
source ../elastic-start-local/.env
echo $ES_LOCAL_API_KEY
```

Open the Drupal installation in the browser (e.g. `http://drupal-ai-agent.ddev.site`).

Go to `/admin/config/system/keys/add` and create a key:

| Field | Value |
|---|---|
| Key name | `Elasticsearch API Key` |
| Key type | `Authentication` |
| Key value | the value of `$ES_LOCAL_API_KEY` |

### 4.2 Configure the provider

Go to `/admin/config/ai/vdb_providers/elasticsearch`:

| Field | Value |
|---|---|
| Elasticsearch Host URL | `http://BRIDGE_IP:9200` (Linux) or `http://host.docker.internal:9200` (Mac/Win) |
| API Key | `Elasticsearch API Key` (the key you just created) |
| Basic Auth Username | leave empty |
| Basic Auth Password | leave empty |
| Index Prefix | `drupal_` (required — the ES index will be named `drupal_content`) |
| Similarity Metric | `Cosine` (recommended for normalized embeddings from commercial LLMs) |
| Enable Hybrid Search | ✅ on if you have a **Platinum / Enterprise** licence (free trial available in Kibana). Pure-kNN-only clusters: leave off — RRF is a paid Elastic feature on 8.x and a basic licence returns 403. Requires ES 8.8+. |
| RRF Rank Constant | `20` (default) |

Save — confirm no connection error is shown.

---

## Section 5: Configure the LM Studio Provider (or any OpenAI-compatible AI provider)

This tutorial uses [LM Studio](https://lmstudio.ai/), which exposes an OpenAI-compatible API on port `1234` by default. The same flow works for LiteLLM (`drupal/ai_provider_litellm`), Ollama (`drupal/ai_provider_ollama`), or OpenAI itself (`drupal/ai_provider_openai`) — only the module name and port differ.

### 5.1 Verify LM Studio is running and exposes the right models

In the **LM Studio app**, open the **Developer** tab and click **Start Server**. Then:

```bash
curl -s http://localhost:1234/v1/models | jq '.data[].id'
```

You must see at least one chat model (e.g. `google/gemma-4-e4b`, `qwen/qwen3-32b`) **and** one embedding model (e.g. `text-embedding-nomic-embed-text-v1.5`). The VDB provider uses the embedding model to generate dense vectors at index time and at query time.

> Some MLX-backend chat models on Apple Silicon can fail to load with `dlopen ... libpython3.11.dylib not found`. If a model loads in LM Studio's chat UI but breaks via the API, swap to a model that uses LM Studio's llama.cpp backend (most non-MLX GGUF models) or reinstall the MLX runtime from LM Studio's Settings → Runtimes.

### 5.2 Configure the provider (no API key needed for LM Studio)

LM Studio doesn't require an API key by default, so skip the Key entity step. Go to `/admin/config/ai/providers/lmstudio`:

| Field | Value |
|---|---|
| Host Name | `http://host.docker.internal` (Mac/Win) or the bridge IP from Section 3.1 (Linux) |
| Port | `1234` |

Save. (You may see a one-time `Could not load the LM Studio API key` notice in `drush watchdog:show` — this is harmless, the AI module logs it whenever a provider is queried without auth and LM Studio doesn't need any.)

### 5.3 Set sensible defaults so other modules pick the right model

```bash
ddev drush ev '
$config = \Drupal::configFactory()->getEditable("ai.settings");
$config->set("default_providers", [
  "chat" => ["provider_id" => "lmstudio", "model_id" => "google/gemma-4-e4b"],
  "embeddings" => ["provider_id" => "lmstudio", "model_id" => "text-embedding-nomic-embed-text-v1.5"],
  "chat_with_tools" => ["provider_id" => "lmstudio", "model_id" => "qwen/qwen3-32b"],
])->save();
'
```

Replace the model IDs with whatever LM Studio reports under `/v1/models`. The `chat_with_tools` model must reliably emit OpenAI-style function calls — Qwen 3, gpt-oss-20b, and recent Gemma instruction-tuned models all work; reasoning-only models often consume their token budget on `<think>` and never emit a tool call.

---

## Section 6: Configure Search API

### 6.1 Add the Elasticsearch server

Go to `/admin/config/search/search-api/add-server`:

| Field | Value |
|---|---|
| Server name | `Elasticsearch` |
| Backend | `AI Search` |

Under **Configure AI Search backend**:

| Field | Value |
|---|---|
| Embeddings Engine | your LM Studio embedding model, e.g. `LM Studio \| text-embedding-nomic-embed-text-v1.5` |
| Tokenizer chat counting model | your LM Studio chat model, e.g. `LM Studio \| google/gemma-4-e4b` |
| Vector Database | `Elasticsearch (Native kNN)` |

Under **Vector Database Configuration** (appears after selecting Elasticsearch):

| Field | Value |
|---|---|
| Database Name | `content` |
| Collection | `content` |
| Similarity Metric | `Cosine Similarity` |

> The "Configure the selected backend" warning in this section and in **Advanced Embeddings Engine Configuration** clears once all required fields are filled and the form is saved.

Under **Advanced Embeddings Engine Configuration**:

| Field | Value |
|---|---|
| Set Dimensions Manually | ☐ off — auto-detected from the embedding model |
| Number of dimensions | `768` for `nomic-embed-text-v1.5`. The form auto-detects this — leave it alone. Different embedding models produce different dimensions (e.g. `text-embedding-3-small` → 1536; `gemini-embedding-001` → 3072). If you switch models later, you must delete the ES index and reindex (see Troubleshooting). |

Under **Advanced Embeddings Strategy Configuration**:

| Field | Value |
|---|---|
| Strategy | `Enriched Embedding Strategy` |
| Maximum chunk size | `500` tokens |
| Minimum chunk overlap for Main Content | `100` tokens |

> **Both chunk values are required, not optional.** If you script the server creation and forget them, the indexer fatals with `Typed property Drupal\ai_search\Plugin\EmbeddingStrategy\EmbeddingBase::$chunkMinOverlap must not be accessed before initialization`. The form's defaults (500 / 100) are sane — leave them alone if you're not sure.

These settings are required. If Vector Database or Embeddings Engine are blank, indexing will fail or produce no vectors.

Save — confirm the green "server could be reached" message.

### 6.2 Create the index

Go to `/admin/config/search/search-api/add-index`:

| Field | Value |
|---|---|
| Index name | `Content` |
| Machine name | `content` — with prefix `drupal_` the ES index will be `drupal_content` |
| Datasources | ✅ `Content` (Article bundle only) **and** ✅ `File` |
| Server | `Elasticsearch` |

> **Why two datasources?** Article bodies live on the node entity and
> file content (PDF / Markdown text extracted by Search API Attachments)
> lives on the file entity. Adding both makes the same index serve
> editorial pages and document uploads — the chatbot in Section 10 then
> retrieves either path identically. With only `Content`, anything you
> upload via `/media/add/document` in §12 will never reach Elasticsearch.

### 6.3 Add fields and mark them for AI Search

Go to `/admin/config/search/search-api/index/content/fields` → **Add fields**.
Each row also has an **AI Search indexing option** column — set both at
the same time:

| Field | Datasource | Type | AI Search indexing option |
|---|---|---|---|
| Title | Content | Fulltext | **Contextual content** |
| Body | Content | Fulltext | **Main content** |
| Content type | Content | String | **Filterable Attributes** |
| Published | Content | Boolean | **Filterable Attributes** |
| Filename | File | String | **Contextual content** |
| File contents *(added by the **File attachments** processor — see §12)* | — | Fulltext | **Main content** |

The form will not save until every field has an indexing option set —
**Ignore** is a valid choice if you want to exclude a field from vector
retrieval. Save changes; the module creates the Elasticsearch index
automatically on the first full index run.

---

## Section 7: Create Content and Index

### 7.1 Create sample articles

Go to `/node/add/article`. Create at least two articles with rich body text on different topics. Longer, more descriptive content produces better dense vector embeddings.

Example: title `GDPR Compliance Guide`, body covering GDPR requirements, data subject rights, 72-hour breach reporting, lawful basis for processing, and step-by-step implementation guidance.

### 7.2 Index

```bash
ddev drush search-api:reset-tracker content
ddev drush search-api:index content
```

The `search-api:reset-tracker` step is required whenever you change:

- the AI Search backend settings,
- the embedding model,
- the Search API field list,
- the AI Search field mapping,
- or newly add PDF extraction fields.

Without a full reset and reindex, Drupal can keep stale tracking state and Elasticsearch will not reflect the latest embedding configuration.

### 7.3 Verify the index was created and vectors are stored

```bash
source ../elastic-start-local/.env
curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cat/indices?v" | grep drupal
# Must show an index named drupal_content
```

Verify vectors are indexed by running a kNN search. On **Elasticsearch 9.x**, the `vector` field is stored in compressed binary form (BBQ quantization) and does **not** appear in `_source` — this is normal. The correct way to confirm vectors are working is a live kNN query:

```bash
source ../elastic-start-local/.env
# Generate a query embedding from LM Studio. Swap the model if you loaded
# something other than nomic-embed-text-v1.5 in §5.
EMB=$(curl -s http://localhost:1234/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-nomic-embed-text-v1.5","input":"GDPR compliance"}' | \
  python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['data'][0]['embedding']))")

# Run kNN search
curl -s -u "elastic:${ES_LOCAL_PASSWORD}" \
  "http://localhost:9200/drupal_content/_search" \
  -H "Content-Type: application/json" \
  -d "{\"knn\":{\"field\":\"vector\",\"query_vector\":${EMB},\"k\":3,\"num_candidates\":50},\"size\":3}" | \
  python3 -c "
import json,sys
d=json.load(sys.stdin)
hits=d.get('hits',{}).get('hits',[])
print(len(hits),'results')
for h in hits: print(' -',h['_source'].get('content','')[:80])
"
# Expected: 3 results with relevant content snippets
```

> **ES 9.x note:** `_source` will not contain a `vector` key — this is expected. Elasticsearch 9.x uses BBQ (Better Binary Quantization) by default for `dense_vector` fields, storing vectors in a compressed index rather than in `_source`. kNN search works correctly despite the field not being visible in `_source`.

---

## Section 8: Advanced — Hybrid Search with RRF

Pure kNN semantic search is powerful but can miss exact keyword matches (SKU numbers, proper nouns, code identifiers). The `ai_vdb_provider_elasticsearch` module is the **only Drupal VDB provider with built-in hybrid search** — it combines kNN dense vector search with BM25 keyword scoring using **Reciprocal Rank Fusion (RRF)** in a single Elasticsearch query, with no manual score normalization required.

Hybrid search was enabled in Section 4 via the **Enable Hybrid Search** toggle. Requires Elasticsearch 8.8+ **and an Elastic Platinum/Enterprise license** — RRF is a commercial feature on the Elastic Stack. Free / basic-license clusters return `403 current license is non-compliant for [Reciprocal Rank Fusion (RRF)]` whenever a hybrid query is executed; in that case leave **Enable Hybrid Search** off and rely on pure kNN. Start a 30-day Platinum trial in Kibana under **Stack Management → License Management → Start trial** to evaluate.

### 8.1 Kibana

Kibana is already running as part of `elastic-start-local` — no extra setup needed. Access it at `http://localhost:5601`.

Log in with username `elastic` and the password from `elastic-start-local/.env` (`ES_LOCAL_PASSWORD`).

### 8.2 Inspect hybrid search results via Kibana Dev Tools

Go to `http://localhost:5601/app/dev_tools#/console`. The module executes hybrid search automatically through Drupal — use Kibana to inspect results and debug. A keyword-only query against the `content` field:

```json
GET drupal_content/_search
{
  "query": {
    "match": { "content": "GDPR compliance" }
  }
}
```

To see what RRF hybrid looks like at the Elasticsearch level, the module combines the kNN (pre-computed by LiteLLM via Drupal) with a BM25 match query internally, ranked with a rank constant of `20` (configurable in the VDB provider settings). Documents appearing high in both the vector and keyword results get the strongest boost — this handles cases where kNN alone would miss an exact product code, but BM25 alone would miss a semantically related concept.

### 8.3 Add the Boost by AI Search processor (optional)

The `ai_search` module ships a **Boost Database by AI Search** processor that blends kNN results into standard Search API queries. Enable it on the index:

Go to `/admin/config/search/search-api/index/content/processors` and enable **Boost Database by AI Search**. Configure the AI Search index to `content` and set a weight between `0.1` (subtle boost) and `1.0` (strong preference for semantic results).

Save and reindex:

```bash
ddev drush search-api:reset-tracker content
ddev drush search-api:index content
```

---

## Section 9: Create the AI Agent

This tutorial uses the `ai_agents` module (not the older `ai_assistant_api`), which is what the **AI Agent Explorer** in Section 10 talks to.

### 9.1 Create the agent

Go to `/admin/config/ai/agents/add`:

| Field | Value |
|---|---|
| Label | `Content Assistant` |
| Machine name | `content_assistant` |

**Agent Instructions (System Prompt):**

```
You are a helpful Drupal content assistant.
To answer ANY question, you MUST call ai_search_rag_search with relevant keywords from the question.
Report titles and summarize content from all results found.
If the search returns nothing relevant, say so clearly rather than guessing.
```

Leave **Swarm orchestration agent** and **Triage agent** unchecked for now (we'll enable swarm in Section 11).

Under **Tools**, search for and add **RAG/Vector Search** only. Remove all other tools — leaving extra tools enabled often makes the LLM avoid the RAG tool and answer from its own knowledge. Save.

### 9.2 Configure RAG tool settings

After saving, edit the agent again at `/admin/config/ai/agents/content_assistant/edit/form`. Scroll down to **Detailed tool usage → RAG/Vector Search**:

| Setting | Value | Why |
|---|---|---|
| ✅ Return directly | on | Without this the LLM loops through searches and gives up |
| ✅ Require Usage | on | Forces the tool to always be called |
| ☐ Use Artifact storage | **off** | Artifact tokens `{{artifact:...}}` are not resolved and break output |

Under **Property restrictions → index**:
- **Action**: `Force value`
- **Hide property**: ✅ on
- **Value**: `content`

> **Important:** the `index` parameter is the **Search API index machine name** (`content`), not the Elasticsearch index name (`drupal_content`). The RAG tool calls `\Drupal::entityTypeManager()->getStorage('search_api_index')->load($index)` internally, so it expects the Drupal-side ID. With `index_prefix=drupal_`, the matching ES index is `drupal_content`, but you don't reference that here.

Save.

### 9.3 Equivalent drush setup (handy if the UI is fiddly)

If you'd rather skip the form clicking, the same agent can be created from drush:

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
      "index" => ["action" => "force_value", "hide_property" => 1, "values" => ["content"]],
    ],
  ],
  "max_loops" => 5,
]);
$agent->save();
echo "Created agent: " . $agent->id() . PHP_EOL;
'
```

---

## Section 10: Test the Agent

Go to `/admin/config/ai/agents/explore`, select **Content Assistant**, and choose a model. For LM Studio pick the chat-with-tools model you set in Section 5.3 (e.g. `LM Studio | qwen/qwen3-32b`).

**Keyword test:**
> "What articles do we have about GDPR compliance?"

**Semantic test (no keyword overlap):**
> "What do we have about data privacy for people in Europe?"

The second query proves kNN semantic search is working — no "GDPR" keyword, but the GDPR article should still come back as the top result. The Progress panel should show:

```
Tool: ai_search_rag_search
index: content
search_string: 'data privacy Europe'
→ Search result: #1 GDPR Compliance Guide...
```

If you'd rather test from the command line without the UI, use this one-liner:

```bash
ddev drush ev '
use Drupal\ai_agents\Task\Task;
use Drupal\ai_agents\PluginInterfaces\AiAgentInterface;
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

## Section 11: Advanced — Multi-Agent Swarm Orchestration

For complex workflows, the `ai_agents` module supports a **Swarm** architecture where a coordinator agent delegates work to specialized sub-agents. This reduces hallucinations and improves accuracy by separating retrieval from synthesis.

### 11.1 Enable Swarm Orchestration on the Content Assistant

Go to `/admin/config/ai/agents/content_assistant/edit/form` and check **Swarm orchestration agent**. This makes the Content Assistant a coordinator that can delegate to other agents.

### 11.2 Create a Relevance Grader agent

Go to `/admin/config/ai/agents/add` and create a second agent:

| Field | Value |
|---|---|
| Label | `Relevance Grader` |
| Machine name | `relevance_grader` |
| AI Provider | LM Studio (use the lightest chat model you have, e.g. `google/gemma-4-e4b`) |

**Instructions:**

```
You are a document relevance grader. You receive a question and a retrieved document chunk.
Respond with only "RELEVANT" if the document genuinely helps answer the question, or "IRRELEVANT" if it does not.
Do not add explanation. One word only.
```

Do not add any tools to this agent. Save.

### 11.3 Wire the Grader into the Swarm

Go back to the Content Assistant edit form. Under **Tools → Select tools**, add **Relevance Grader** as a sub-agent tool alongside RAG/Vector Search.

Update the Content Assistant instructions:

```
You are a Drupal content assistant coordinating a two-stage retrieval workflow.

Step 1: Call ai_search_rag_search to retrieve candidate documents (the index is already forced to `content`).
Step 2: For each retrieved chunk, call relevance_grader to score it RELEVANT or IRRELEVANT.
Step 3: Use only RELEVANT chunks to formulate your final answer.

If no chunks are RELEVANT, say so clearly rather than guessing.
```

This two-stage pattern dramatically reduces noise — the grader filters out tangentially related content before the answer is generated.

### 11.4 Test the swarm

Go to `/admin/config/ai/agents/explore`, select **Content Assistant**, and ask a question that would previously return low-quality results. The Progress panel will now show two agent steps: the RAG retrieval and the grader evaluation.

---

## Section 12: Index PDFs and text documents

The `ai_vdb_provider_elasticsearch` module ships its own pure-PHP PDF
extractor (plugin id `php_pdfparser_extractor`, backed by
`smalot/pdfparser`) and registers `text/*` MIME types for `.md`, `.rst`,
`.org`, `.adoc`, `.log`, `.yaml`, `.ini`, `.toml`. **No system binaries
required** — the same setup works on every Drupal host without
`apt-get install poppler-utils`, Java, or Tika Server.

### 12.1 Configure the extractor

Go to `/admin/config/search/search-api-attachments` and set:

| Field | Value |
|---|---|
| Extraction method | `PHP PdfParser (smalot/pdfparser)` |
| **Read text files directly** | ✅ on — lets `.md`, `.txt`, `.rst`, etc. flow through without an extractor binary |

Save.

> **If `PHP PdfParser` isn't in the Extraction method dropdown**, the
> alpha you installed predates the bundled extractor. Two options:
> (a) update — `ddev composer update drupal/ai_vdb_provider_elasticsearch`;
> or (b) fall back to `Pdftotext Extractor` and install poppler-utils
> in the web container — `ddev exec sudo apt-get update && sudo apt-get install -y poppler-utils`.
> The pure-PHP path is the recommended one but pdftotext works just as
> well for indexing.

### 12.2 Allow document extensions on the Document media type

Drupal's standard install ships a *Document* media type. Edit
`field_media_document` at `/admin/structure/media/manage/document` →
**Manage fields** → *Document file* → **Allowed file extensions**:

```
pdf md markdown rst org adoc asciidoc txt log yaml yml ini conf toml
```

Save.

> Don't create a *second* Document media type — the form ends up with
> two file widgets and submission fails with *"Document field is required"*.

### 12.3 Enable the File attachments processor on the index

Go to `/admin/config/search/search-api/index/content/processors`, tick
**File attachments**, save.

The *File contents* field you added in §6.3 now starts producing extracted
text on save. (If you didn't add it earlier, do it now: *Fields → Add
fields → File datasource → "the file" → File contents*, type Fulltext,
indexing option **Main content**.)

### 12.4 Upload and verify

Go to `/media/add/document` (Content → Media → Add media → Document) and
upload any PDF, Markdown, or `.txt` file. Save.

With Search API's default `index_directly: TRUE`, embedding happens on
save — no manual reindex needed for routine uploads. Force a full reset
only when you change extractor / fields / models:

```bash
ddev drush search-api:status content    # tracks % indexed
ddev drush search-api:index content     # flush the queue (cron does this on schedule)
```

Verify both flows landed in Elasticsearch:

```bash
source ../elastic-start-local/.env
curl -s -u "elastic:${ES_LOCAL_PASSWORD}" \
  "http://localhost:9200/drupal_content/_count"
```

Both PDFs (extracted via `smalot/pdfparser`) and Markdown files (read
directly because their MIME type is `text/markdown`) end up as embedded
chunks in the same index — the chatbot from Section 10 retrieves them
identically.

---

## Section 13: Browse with Kibana

Kibana is already running as part of `elastic-start-local`. Go to `http://localhost:5601` and log in with username `elastic` and the `ES_LOCAL_PASSWORD` value from `../elastic-start-local/.env`.

### Create a Data View

Go to `http://localhost:5601/app/management/kibana/dataViews` → **Create data view**:
- Name: `Drupal Content`, Index pattern: `drupal_content`, No timestamp field.

### Discover

Go to `http://localhost:5601/app/discover`, select **Drupal Content**. Expand any row to see `content` (indexed text), `entity_id`, `bundle`, and `chunk_id`. The `vector` field is **not shown in Discover** on ES 9.x — it is stored internally in compressed BBQ format for kNN search and excluded from `_source` by default.

### Dev Tools console (`http://localhost:5601/app/dev_tools#/console`)

**Keyword search** (against the `content` field):
```json
GET drupal_content/_search
{"query": {"match": {"content": "GDPR compliance"}}}
```

**Check vectors are indexed** (ES 9.x: `vector` is not in `_source` but IS indexed for kNN):
```json
GET drupal_content/_mapping
```
Look for `"vector": { "type": "dense_vector", "dims": 3072, "index_options": { "type": "bbq_hnsw" } }` — this confirms the vector field is mapped. The absence of `vector` in `_source` is normal ES 9.x behaviour.

**Hybrid search** — the module sends this internally when hybrid is enabled (rank constant configurable in VDB provider settings, default 20):
```json
GET drupal_content/_search
{
  "retriever": {
    "rrf": {
      "retrievers": [
        {
          "standard": {
            "query": {"match": {"content": "data privacy Europe"}}
          }
        }
      ],
      "rank_constant": 20,
      "rank_window_size": 100
    }
  }
}
```

> The kNN retriever leg is added automatically by the module at query time using a pre-computed vector from LiteLLM. You cannot replicate the full hybrid query in Kibana Dev Tools without a pre-computed vector array.

---

## Section 14: Optional — MCP Server Integration

Enables external AI assistants (Claude, Cursor) to interact with Drupal via Model Context Protocol.

> **Known issue:** The Composer release has a broken dependency (`drupal/simple_oauth_21` doesn't exist). Install by cloning directly:

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

**VDB provider cannot reach Elasticsearch**
Elasticsearch runs outside the DDEV network. The VDB provider host must be `http://BRIDGE_IP:9200` (Linux) or `http://host.docker.internal:9200` (Mac/Win) — not `localhost:9200` or `elasticsearch:9200`.

On Linux the `elastic-start-local` installer binds ES to `127.0.0.1` by default. If you skipped the fix in Section 3, run it now:
```bash
sed -i 's/127\.0\.0\.1:\${ES_LOCAL_PORT}/0.0.0.0:${ES_LOCAL_PORT}/' ../elastic-start-local/docker-compose.yml
(cd ../elastic-start-local && docker compose down && docker compose up --wait)
```

Then verify the bridge IP is reachable from inside DDEV:
```bash
HOST_IP=$(ddev exec ip route | grep default | awk '{print $3}')
source ../elastic-start-local/.env
ddev exec curl -s -u "elastic:${ES_LOCAL_PASSWORD}" "http://${HOST_IP}:9200/_cluster/health"
```

**Elasticsearch index not created after indexing**
Check that the VDB provider is saved correctly and the Search API server uses the `AI Search` backend. Then trigger a full reindex:
```bash
ddev drush search-api:reset-tracker content
ddev drush search-api:index content
source ../elastic-start-local/.env && curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cat/indices?v"
```

Also verify the server backend settings are not blank for:
- Vector Database
- Embeddings Engine
- Tokenizer chat counting model

**PDF uploads are indexed but not found by semantic queries**
Check `/admin/config/search/search-api/index/content/fields` and ensure the extracted **File contents** field is set to **Main content** or **Contextual content** (not **Ignore**). Then reindex:
```bash
ddev drush search-api:reset-tracker content
ddev drush search-api:index content
```

**Markdown / RST / Org / etc. uploads come back as `application/octet-stream`**
The `ai_vdb_provider_elasticsearch` module registers `text/*` MIME types
for `.md`, `.markdown`, `.rst`, `.org`, `.adoc`, `.asciidoc`, `.log`,
`.yaml`, `.yml`, `.ini`, `.conf`, `.toml`. They only flow through the
plain-text extractor when **Read text files directly** is enabled at
`/admin/config/search/search-api-attachments`. Confirm the toggle is on,
then reindex.

**Hybrid search returns 403 `license non-compliant for [Reciprocal Rank Fusion (RRF)]`**
RRF is a paid Elastic feature. Either start a Platinum trial in Kibana
(Stack Management → License Management) or disable hybrid search:
```bash
ddev drush ev '\Drupal::configFactory()
  ->getEditable("ai_vdb_provider_elasticsearch.settings")
  ->set("hybrid_search", FALSE)->save();'
```
Pure kNN works on the basic / free license.

**A document I uploaded via Media isn't searchable / an Article body isn't searchable**
The index needs **both** `entity:node` (Article) and `entity:file` datasources — see §6.2. Edit at `/admin/config/search/search-api/index/content/edit`, tick whichever is missing, add the matching fields on the *Fields* tab (Title/Body for nodes; Filename + the processor-added *File contents* for files), set the AI Search indexing option on each, then run `ddev drush search-api:reset-tracker content && ddev drush search-api:index content`. To repair the node side from drush in one shot:

```bash
ddev drush ev '
use Drupal\search_api\Entity\Index;
use Drupal\search_api\Item\Field;
$index = Index::load("content");
$ds = \Drupal::service("search_api.plugin_helper")
  ->createDatasourcePlugin($index, "entity:node", [
    "bundles"   => ["default" => 0, "selected" => ["article"]],
    "languages" => ["default" => 1, "selected" => []],
  ]);
$index->addDatasource($ds);
foreach ([["node_title","title","string"],["node_body","body","text"]] as [$id,$path,$type]) {
  if (!$index->getField($id)) {
    $f = new Field($index, $id);
    $f->setDatasourceId("entity:node"); $f->setPropertyPath($path);
    $f->setType($type); $f->setLabel(ucfirst($path));
    $index->addField($f);
  }
}
$index->save();
\Drupal::configFactory()->getEditable("ai_search.index.content")
  ->set("indexing_options", \Drupal::config("ai_search.index.content")->get("indexing_options")
    + ["node_title" => ["indexing_option" => "contextual_content"],
       "node_body"  => ["indexing_option" => "main_content"]])
  ->save();
'
ddev drush search-api:reset-tracker content && ddev drush search-api:index content
```

**Content exists but RAG still returns no useful results**
Check `/admin/config/search/search-api/index/content/fields` and make sure:
- **Body** is **Main content**
- **Title** is **Contextual content**
- filter fields such as **Content type** and **Published** are **Attributes**

Then run a full reindex:
```bash
ddev drush search-api:reset-tracker content
ddev drush search-api:index content
```

**Mapping error on indexing (vector dimension mismatch)**
The `dense_vector` dimensions in Elasticsearch must match the embedding model exactly (e.g. 1536 for `text-embedding-3-small`). If you switched models, delete the ES index and re-index:
```bash
source ../elastic-start-local/.env && curl -u "elastic:${ES_LOCAL_PASSWORD}" -X DELETE "http://localhost:9200/drupal_content"
ddev drush search-api:reset-tracker content && ddev drush search-api:index content
```

**No embedding model available in your AI provider**
The VDB provider requires an embedding model (e.g. `text-embedding-nomic-embed-text-v1.5` via LM Studio, `text-embedding-3-small` via OpenAI, or `nomic-embed-text` via Ollama). For LM Studio:
```bash
curl -s http://localhost:1234/v1/models | jq '.data[].id' | grep embed
```
If the list is empty, load an embedding model from LM Studio's **Discover** tab.

**Agent uses the wrong index name (e.g. `articles`, `drupal_content`, or anything other than `content`)**
The RAG tool's `index` parameter is the **Search API** index machine name (`content`), not the Elasticsearch index name. If the Property setup UI doesn't save reliably, set it from drush:

```bash
ddev drush ev '
$agent = \Drupal\ai_agents\Entity\AiAgent::load("content_assistant");
$limits = $agent->get("tool_usage_limits") ?? [];
$limits["ai_search:rag_search"] = ["index" => ["action" => "force_value", "hide_property" => 1, "values" => ["content"]]];
$agent->set("tool_usage_limits", $limits)->save();
'
```

**Agent returns `{{artifact:ai_search_rag_search:1}}`**
Uncheck **Use Artifact storage** in the agent's RAG/Vector Search tool settings.

**Agent loops through many searches then says "Not Solvable"**
Re-enable **Return directly** on the RAG/Vector Search tool.

**Composer stability errors**
```bash
ddev composer config minimum-stability alpha && ddev composer config prefer-stable true
```

**LM Studio (or LiteLLM/Ollama) not reachable from DDEV (Linux)**
```bash
ddev exec ip route | grep default | awk '{print $3}'
```
Use that bridge IP for the host in `/admin/config/ai/providers/lmstudio` — `host.docker.internal` only works on Mac/Windows.

**Drupal logs `Could not load the LM Studio API key` on every request**
Harmless. The AI module's base provider class always tries to look up an `api_key` config value, but LM Studio doesn't need one. The request still completes successfully. To silence the noise, create a dummy Key entity (`/admin/config/system/keys/add` → key value `none`) and set `ai_provider_lmstudio.settings.api_key` to its ID via drush.

**LM Studio chat returns reasoning_content but empty content / finish_reason=length**
You picked a reasoning-trained model (e.g. `mistralai/ministral-3-14b-reasoning`) and it consumed the entire token budget on its `<think>` block. Pick a non-reasoning model like `google/gemma-4-e4b` or `qwen/qwen3-32b` for tool-calling, or raise `max_tokens` significantly.

**DDEV project named incorrectly**
Run from inside `drupal-ai-agent`:
```bash
ddev stop
ddev config --project-type=drupal11 --docroot=web && ddev start
```

---

## Quick Reference

| Task | Command |
|---|---|
| Start Elasticsearch + Kibana | `../elastic-start-local/start.sh` |
| Stop Elasticsearch + Kibana | `../elastic-start-local/stop.sh` |
| Start DDEV | `ddev start` |
| Stop DDEV | `ddev stop` |
| Restart DDEV | `ddev restart` |
| Check index progress | `ddev drush search-api:status content` |
| Reindex | `ddev drush search-api:reset-tracker content && ddev drush search-api:index content` |
| Check ES indices | `source ../elastic-start-local/.env && curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cat/indices?v" \| grep drupal` |
| Check ES health | `source ../elastic-start-local/.env && curl -u "elastic:${ES_LOCAL_PASSWORD}" "http://localhost:9200/_cluster/health?pretty"` |
| Agent Explorer | `ddev launch /admin/config/ai/agents/explore` |
| Kibana | `http://localhost:5601` |
| Bridge IP (Linux) | `ddev exec ip route \| grep default \| awk '{print $3}'` |
| Rebuild cache | `ddev drush cr` |

---

## Cleanup

```bash
ddev stop && ddev delete -O -y
docker volume prune -f && docker image prune -f
```

---

## Additional Resources

- [Drupal AI Module](https://www.drupal.org/project/ai)
- [AI VDB Provider Elasticsearch](https://www.drupal.org/project/ai_vdb_provider_elasticsearch)
- [Elasticsearch dense_vector Documentation](https://www.elastic.co/guide/en/elasticsearch/reference/current/dense-vector.html)
- [Elasticsearch kNN search](https://www.elastic.co/guide/en/elasticsearch/reference/current/knn-search.html)
- [DDEV Documentation](https://ddev.readthedocs.io/)
- [LM Studio](https://lmstudio.ai/)
- [LiteLLM Documentation](https://docs.litellm.ai/)
- [MCP Server Module](https://www.drupal.org/project/mcp_server)
