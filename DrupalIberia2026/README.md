# Drupal AI Agent with Elasticsearch

Build a RAG-powered AI agent inside Drupal that answers questions using your content, indexed in Elasticsearch with kNN dense vector semantic search, answered by an LLM.

```
User question → Drupal AI Agent → RAG search (AI Search VDB)
  → Elasticsearch kNN dense vectors → LiteLLM → Grounded answer
```

## Prerequisites

- Docker 20.10+, DDEV (latest), Git, curl, jq
- LiteLLM proxy running with at least one chat model **and** one embedding model
- 8GB RAM, 20GB free disk
- Ports 9200 (Elasticsearch), 33000 (DDEV) free

```bash
docker --version && ddev version && git --version && curl --version
```

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

> If the command fails because the directory is not empty, remove the empty `web/` folder first: `rmdir web`

```bash
ddev start
ddev status   # Project name must show 'drupal-ai-agent'
```

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
  'drupal/ai_provider_litellm' \
  'drupal/key' \
  'drupal/search_api' \
  'drupal/search_api_attachments' \
  'drupal/ai_vdb_provider_elasticsearch:^1.0@alpha'
```

> The `elastic/elasticsearch` PHP client ^8.0 is pulled in automatically — no need to require it separately.

> **Never install `drupal/elasticsearch_connector`** — it conflicts with the Search API server form and causes a `PluginException`.

### 2.3 Install Drupal and enable modules

```bash
ddev drush site:install --account-name=admin --account-pass=admin --yes

ddev drush pm:enable \
  ai ai_search ai_provider_litellm key \
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

Open the Drupal instalation in the browser (Eg. `http://drupal-ai-agent.ddev.site:33000`)

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

## Section 5: Configure LiteLLM or any AI other Provider

This tutorial uses litellm provider but you can use any other like OpenAI, Anthropic, Gemini, etc.

### 5.1 Verify LiteLLM is running

```bash
curl http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_API_KEY"
```

Confirm the output includes at least one **chat** model and at least one **embedding** model. The VDB provider uses the embedding model to generate dense vectors at index time and at query time.

### 5.2 Store the key and configure the provider

Go to `/admin/config/system/keys/add` and create a key with your `$LITELLM_API_KEY` value.

Then go to `/admin/config/ai/providers` → LiteLLM or your provider of choice:

| Field | Value |
|---|---|
| API Key | the key you just created |
| Host | `http://BRIDGE_IP:4000` (Linux) or `http://host.docker.internal:4000` (Mac/Win) |

Save — confirm green status.

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
| Embeddings Engine | your LiteLLM embedding model, e.g. `LiteLLM Proxy \| gemini-embedding-001` |
| Tokenizer chat counting model | your LiteLLM chat model, e.g. `LiteLLM Proxy \| gemini-flash-latest` |
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
| Number of dimensions | `3072` (auto-filled, do not change) |

Under **Advanced Embeddings Strategy Configuration**:

| Field | Value |
|---|---|
| Strategy | `Enriched Embedding Strategy` |
| Maximum chunk size | `500` tokens |
| Minimum chunk overlap for Main Content | `100` tokens |

These settings are required. If Vector Database or Embeddings Engine are blank, indexing will fail or produce no vectors.

Save — confirm the green "server could be reached" message.

### 6.2 Create the index

Go to `/admin/config/search/search-api/add-index`:

| Field | Value |
|---|---|
| Index name | `Content` |
| Machine name | `content` — with prefix `drupal_` the ES index will be `drupal_content` |
| Datasources | `Content` → Article bundle only |
| Server | `Elasticsearch` |

### 6.3 Add fields

Go to `/admin/config/search/search-api/index/content/fields` → Add:

| Field | Type |
|---|---|
| Title | **Fulltext** |
| Body | **Fulltext** |
| Content type | String |
| Published | Boolean |

Save changes. The module will create the Elasticsearch index automatically on the first full index run.

### 6.3.1 Mark each Search API field for AI Search explicitly

This step is required for vector search. Adding fields to the Search API index is not enough by itself.

On the same `/admin/config/search/search-api/index/content/fields` page, each field has an **AI Search indexing option** column. Set:

| Label | Machine name | Type | Indexing option |
|---|---|---|---|
| Body | `body` | Fulltext | **Main content** |
| Title | `title` | Fulltext | **Contextual content** |
| Content type | `type` | String | **Filterable Attributes** |
| Published | `status` | Boolean | **Filterable Attributes** |

The form will not save until every field has an option selected — **Ignore** is a valid choice if you want to exclude a field from vector retrieval.

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
# Generate an embedding from LiteLLM (replace model and key as needed)
EMB=$(curl -s -H "Authorization: Bearer $LITELLM_API_KEY" \
  "http://YOUR_LITELLM_HOST:4000/v1/embeddings" \
  -H "Content-Type: application/json" \
  -d '{"model": "YOUR_EMBEDDING_MODEL", "input": "GDPR compliance"}' | \
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

### 9.1 Create the agent

Go to `/admin/config/ai/ai-assistant/add`:

| Field | Value |
|---|---|
| Label | `Content Assistant` |
| Machine name | `content_assistant` |
| AI Provider | LiteLLM Proxy |

**Instructions:**

```
You are a helpful Drupal content assistant.
To answer ANY question, you MUST call ai_search_rag_search with:
- index: drupal_content
- search_string: relevant keywords from the question

The index name is ALWAYS drupal_content. Never use any other index name.
Report titles and summarize content from all results found.
```

Under **Tools**, add **RAG/Vector Search** only. Remove all other tools. Save.

This is required for the first working setup. Do not leave unrelated tools enabled, or the assistant may avoid the RAG tool and answer without retrieval.

### 9.2 Fix tool settings (UI bug workaround)

The agent UI does not reliably persist tool settings to the config entity. Set directly:

```bash
ddev drush ev "
\$config = \Drupal::configFactory()->getEditable('ai_assistant_api.ai_assistant.content_assistant');
\$config->set('actions_enabled', ['ai_search_rag_search' => 'ai_search_rag_search']);
\$config->save();
echo 'Saved: ';
print_r(\$config->get('actions_enabled'));
"
ddev drush cr
```

### 9.3 Configure RAG tool settings

Go to `/admin/config/ai/agents/content_assistant/edit/form` → **Tools → RAG/Vector Search → Detailed tool usage**:

| Setting | Value | Why |
|---|---|---|
| ✅ Return directly | on | Without this the LLM loops through searches and gives up |
| ✅ Require Usage | on | Forces the tool to always be called |
| ☐ Use Artifact storage | **off** | Artifact tokens `{{artifact:...}}` are not resolved and break output |

Under **Property setup → Restrictions for property index**:
- Set to **Force value** + **Hide property**
- Value: `drupal_content`

Save.

This is required. If you leave the index unrestricted, the agent can call the tool with the wrong index name and retrieval will silently miss your content.

---

## Section 10: Test the Agent

Go to `/admin/config/ai/agents/explore`, select **Content Assistant**, choose a model (e.g. `gemini-flash-latest`).

**Keyword test:**
> "What articles do we have about GDPR compliance?"

**Semantic test (no keyword overlap):**
> "What do we have about data privacy for people in Europe?"

The second query proves kNN semantic search is working — no "GDPR" keyword, but the article should still be found. The Progress panel should show:

```
Tool: ai_search_rag_search
index: drupal_content
search_string: 'data privacy Europe'
→ Search result: #1 ...
```

---

## Section 11: Advanced — Multi-Agent Swarm Orchestration

For complex workflows, the `ai_agents` module supports a **Swarm** architecture where a coordinator agent delegates work to specialized sub-agents. This reduces hallucinations and improves accuracy by separating retrieval from synthesis.

### 11.1 Enable Swarm Orchestration on the Content Assistant

Go to `/admin/config/ai/agents/content_assistant/edit/form` and check **Swarm orchestration agent**. This makes the Content Assistant a coordinator that can delegate to other agents.

### 11.2 Create a Relevance Grader agent

Go to `/admin/config/ai/ai-assistant/add` and create a second agent:

| Field | Value |
|---|---|
| Label | `Relevance Grader` |
| Machine name | `relevance_grader` |
| AI Provider | LiteLLM Proxy (use a fast, cheap model like `gemini-flash-latest`) |

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

Step 1: Call ai_search_rag_search with index=drupal_content to retrieve candidate documents.
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

### 12.2 Add a Document media field to Article

Drupal's standard install ships a *Document* media type with a
`field_media_document` file field. Reuse it (don't create a duplicate
type — that yields two file widgets and a *"Document field is required"*
error). Edit it at `/admin/structure/media/manage/document` → Manage
fields → `field_media_document` → **Allowed file extensions**:

```
pdf md markdown rst org adoc asciidoc txt log yaml yml ini conf toml
```

Then add a **Media** reference field to Article at
`/admin/structure/types/manage/article/fields/add-field`, type
*Reference → Media*, allowing the *Document* bundle. Or upload directly
via `/media/add/document` and reference the Media entity from a node body
embed.

### 12.3 Wire the index

Go to `/admin/config/search/search-api/index/content/processors` and
enable **File attachments** (provided by `search_api_attachments`). Save.

Then on `/admin/config/search/search-api/index/content/fields` → **Add
fields** → expand the file field → add the extracted text property
(usually labelled *File contents* or *Whole file entity*) as **Fulltext**.
Set its **AI Search indexing option** to **Main content** (or
**Contextual content**). Save.

> If the field is left as **Ignore**, text gets extracted but never
> embedded, and semantic queries silently miss those documents.

### 12.4 Upload and reindex

Upload a PDF, Markdown file, or `.txt` to a Document media item (or
directly to the Article via the file field), then trigger indexing:

```bash
ddev drush search-api:reset-tracker content
ddev drush search-api:index content
```

You can verify both flows landed in Elasticsearch:

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

**I created an Article but its body isn't in Elasticsearch**
The default index in this tutorial is over `entity:node` (Article bundle)
*and* `entity:file`. If you scaffolded with an older configuration that
only had `entity:file`, add `entity:node` as a second datasource at
`/admin/config/search/search-api/index/content/edit`, add **Title** and
**Body** fields, mark them *Contextual content* / *Main content* in the
AI Search column, and reindex.

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

**No embedding model available in LiteLLM**
The VDB provider requires an embedding model (e.g. `text-embedding-3-small` via OpenAI, or a local `nomic-embed-text` via Ollama). Verify with:
```bash
curl http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_API_KEY" | jq '.data[].id'
```

**Agent uses `articles` instead of `drupal_content` as index**
The Property setup UI doesn't save reliably. Ensure the instructions explicitly name `drupal_content` and the Force value is set in Property setup.

**Agent returns `{{artifact:ai_search_rag_search:1}}`**
Uncheck **Use Artifact storage** in the agent's RAG/Vector Search tool settings.

**Agent loops through many searches then says "Not Solvable"**
Re-enable **Return directly** on the RAG/Vector Search tool.

**Composer stability errors**
```bash
ddev composer config minimum-stability alpha && ddev composer config prefer-stable true
```

**LiteLLM not reachable from DDEV (Linux)**
```bash
ddev exec ip route | grep default | awk '{print $3}'
```
Use that IP for the LiteLLM host in the provider settings — `host.docker.internal` only works on Mac/Windows.

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
- [LiteLLM Documentation](https://docs.litellm.ai/)
- [MCP Server Module](https://www.drupal.org/project/mcp_server)
