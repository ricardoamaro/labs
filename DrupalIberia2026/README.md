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
cd labs/DrupalIberia2026/drupal-ai-agent
```

> **Critical:** Always run `ddev config` from inside `drupal-ai-agent`. Running it from a parent directory attaches DDEV to the wrong project and breaks everything.

```bash
ddev config --project-type=drupal11 --docroot=web
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

Add Elasticsearch as a DDEV service by creating a Docker Compose override file. Run this from inside `drupal-ai-agent`:

```bash
cat > .ddev/docker-compose.elasticsearch.yaml << 'EOF'
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.0
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
    ports:
      - "9200:9200"
EOF
```

Restart DDEV to bring up the Elasticsearch container:

```bash
ddev restart
```

Verify Elasticsearch is running:

```bash
curl "http://localhost:9200/_cluster/health?pretty"
# Expected: "status": "green" or "yellow"
```

> **Note:** `ddev describe` may show the `elasticsearch` service as `stopped` — this is a display quirk for custom docker-compose services. The `curl` response above is the authoritative health check.

### 3.1 Get the Docker bridge IP (Linux only — needed for LiteLLM)

On Linux, `host.docker.internal` does not work automatically. Get the IP DDEV containers use to reach the host:

```bash
HOST_IP=$(ddev exec ip route | grep default | awk '{print $3}')
echo "Bridge IP: $HOST_IP"
```

> **macOS/Windows:** Use `host.docker.internal` instead of the bridge IP when configuring LiteLLM.

> Elasticsearch runs inside the DDEV Docker network and is always reachable at `http://elasticsearch:9200` from within Drupal — no bridge IP needed for ES.

---

## Section 4: Configure the VDB Provider

Go to `/admin/config/ai/vdb_providers/elasticsearch`:

| Field | Value |
|---|---|
| Elasticsearch Host URL | `http://elasticsearch:9200` |
| API Key | None (leave empty — security is disabled for local dev) |
| Index Prefix | leave empty (ES index name = Search API machine name) |
| Similarity Metric | `Cosine` (recommended for normalized embeddings from commercial LLMs) |
| Enable Hybrid Search | ✅ on — combines kNN + BM25 via RRF automatically (requires ES 8.8+) |
| RRF Rank Constant | `20` (default) |

Save — confirm no connection error is shown.

---

## Section 5: Configure LiteLLM Provider

### 5.1 Verify LiteLLM is running

```bash
curl http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_API_KEY"
```

Confirm the output includes at least one **chat** model and at least one **embedding** model. The VDB provider uses the embedding model to generate dense vectors at index time and at query time.

### 5.2 Store the key and configure the provider

Go to `/admin/config/system/keys/add` and create a key with your `$LITELLM_API_KEY` value.

Then go to `/admin/config/ai/providers` → LiteLLM:

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
| Backend | `AI Search (VDB)` |
| VDB Provider | `Elasticsearch` |
| AI Provider | `LiteLLM Proxy` (used for embedding generation) |

Save — confirm the green "server could be reached" message.

### 6.1.1 Verify the AI Search backend settings explicitly

After saving, edit the server again and confirm these backend settings are present:

| Setting | Value |
|---|---|
| Vector Database | `Elasticsearch` |
| Embeddings engine | your LiteLLM embedding model, for example `litellm__gemini-embedding-001` |
| Tokenizer chat counting model | your LiteLLM chat model, for example `litellm__gemini-flash-latest` |
| Embedding strategy | `contextual_chunks` |
| Chunk size | `500` |
| Chunk minimum overlap | `100` |

These settings are required. If the Vector Database, embeddings engine, or chat model are blank, indexing will fail or produce no vectors.

### 6.2 Create the index

Go to `/admin/config/search/search-api/add-index`:

| Field | Value |
|---|---|
| Index name | `Drupal Content` |
| Machine name | `drupal_content` ← must match exactly |
| Datasources | `Content` → Article bundle only |
| Server | `Elasticsearch` |

### 6.3 Add fields

Go to `/admin/config/search/search-api/index/drupal_content/fields` → Add:

| Field | Type |
|---|---|
| Title | **Fulltext** |
| Body | **Fulltext** |
| Content type | String |
| Published | Boolean |

Save changes. The module will create the Elasticsearch index automatically on the first full index run.

### 6.3.1 Mark each Search API field for AI Search explicitly

This step is required for vector search. Adding fields to the Search API index is not enough by itself.

Go to `/admin/config/search/search-api/index/drupal_content/fields/ai_search` and set:

| Field | AI Search indexing option |
|---|---|
| Body | **Main content** |
| Title | **Contextual content** |
| Content type | **Attributes** |
| Published | **Attributes** |

Do not leave these fields as **Ignore** unless you intentionally want them excluded from vector retrieval.

---

## Section 7: Create Content and Index

### 7.1 Create sample articles

Go to `/node/add/article`. Create at least two articles with rich body text on different topics. Longer, more descriptive content produces better dense vector embeddings.

Example: title `GDPR Compliance Guide`, body covering GDPR requirements, data subject rights, 72-hour breach reporting, lawful basis for processing, and step-by-step implementation guidance.

### 7.2 Index

```bash
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
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
curl "http://localhost:9200/_cat/indices?v"
# Must show an index named drupal_content
```

Check that documents have the `vector` field populated:

```bash
curl "http://localhost:9200/drupal_content/_search?pretty&size=1" | python3 -m json.tool | grep -c '"vector"'
# Expected: 1 (vector field present in the document)
```

> The `vector` field holds the dense vector embedding generated by LiteLLM. Semantic search is executed by Drupal via the AI Search VDB layer — test it through the agent in Section 10.

---

## Section 8: Advanced — Hybrid Search with RRF

Pure kNN semantic search is powerful but can miss exact keyword matches (SKU numbers, proper nouns, code identifiers). The `ai_vdb_provider_elasticsearch` module is the **only Drupal VDB provider with built-in hybrid search** — it combines kNN dense vector search with BM25 keyword scoring using **Reciprocal Rank Fusion (RRF)** in a single Elasticsearch query, with no manual score normalization required.

Hybrid search was enabled in Section 4 via the **Enable Hybrid Search** toggle. Requires Elasticsearch 8.8+.

### 8.1 Add Kibana as an optional DDEV service

```bash
cat >> .ddev/docker-compose.elasticsearch.yaml << 'EOF'
  kibana:
    image: docker.elastic.co/kibana/kibana:8.13.0
    environment:
      - ELASTICSEARCH_HOSTS=http://elasticsearch:9200
    ports:
      - "5601:5601"
    depends_on:
      - elasticsearch
EOF

ddev restart
```

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

Go to `/admin/config/search/search-api/index/drupal_content/processors` and enable **Boost Database by AI Search**. Configure the AI Search index to `drupal_content` and set a weight between `0.1` (subtle boost) and `1.0` (strong preference for semantic results).

Save and reindex:

```bash
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
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

## Section 12: Index PDFs

### 12.1 Setup

```bash
ddev exec which pdftotext || ddev exec apt-get install -y poppler-utils
```

Go to `/admin/config/search/search-api-attachments` and set extractor to `pdftotext`.

### 12.2 Add a File field to your content type

Go to `/admin/structure/types/manage/article/fields/add-field`. Add a **File** field allowing PDF uploads.

### 12.3 Add file contents to the index

Go to `/admin/config/search/search-api/index/drupal_content/fields` → **Add fields** → find **File contents** under your file field → add as **Fulltext**. Save.

### 12.4 Enable the file extraction processor

Go to `/admin/config/search/search-api/index/drupal_content/processors` and enable the **File attachments** processor provided by `search_api_attachments`. Save.

### 12.5 Mark PDF extracted text for vector embedding (required)

This step is required. If you skip it, PDF text can be extracted but not embedded, and semantic RAG queries will miss those documents.

Go to `/admin/config/search/search-api/index/drupal_content/fields/ai_search` and set the indexing option for your extracted **File contents** field to:

- **Main content** (recommended), or
- **Contextual content**

Do **not** leave it as **Ignore**.

Then save.

The module will automatically extract text from uploaded PDFs and include it in the fields sent to Elasticsearch for embedding — no manual pipeline update needed.

Upload a PDF to any article, then reindex:

```bash
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
```

---

## Section 13: Browse with Kibana

If you have not already added Kibana (see Section 8.1), add it now and restart DDEV. Then go to `http://localhost:5601`.

### Create a Data View

Go to `http://localhost:5601/app/management/kibana/dataViews` → **Create data view**:
- Name: `Drupal Content`, Index pattern: `drupal_content`, No timestamp field.

### Discover

Go to `http://localhost:5601/app/discover`, select **Drupal Content**. Expand any row to see `content` (indexed text), `vector` (the dense embedding), `entity_id`, `bundle`, and `chunk_id`.

### Dev Tools console (`http://localhost:5601/app/dev_tools#/console`)

**Keyword search** (against the `content` field):
```json
GET drupal_content/_search
{"query": {"match": {"content": "GDPR compliance"}}}
```

**Check vector field exists** (confirms embeddings are stored):
```json
GET drupal_content/_search
{
  "size": 1,
  "_source": ["entity_id", "chunk_id", "content"],
  "query": {"exists": {"field": "vector"}}
}
```

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
Verify the host URL is exactly `http://elasticsearch:9200` (the internal DDEV Docker hostname). `localhost:9200` will not work from inside the container.
```bash
ddev exec curl -s http://elasticsearch:9200/_cluster/health
```

**Elasticsearch index not created after indexing**
Check that the VDB provider is saved correctly and the Search API server uses the `AI Search (VDB)` backend. Then trigger a full reindex:
```bash
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
curl "http://localhost:9200/_cat/indices?v"
```

Also verify the server backend settings are not blank for:
- Vector Database
- Embeddings engine
- Tokenizer chat counting model

**PDF uploads are indexed but not found by semantic queries**
Check `/admin/config/search/search-api/index/drupal_content/fields/ai_search` and ensure the extracted **File contents** field is set to **Main content** or **Contextual content** (not **Ignore**). Then reindex:
```bash
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
```

**Content exists but RAG still returns no useful results**
Check `/admin/config/search/search-api/index/drupal_content/fields/ai_search` and make sure:
- **Body** is **Main content**
- **Title** is **Contextual content**
- filter fields such as **Content type** and **Published** are **Attributes**

Then run a full reindex:
```bash
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
```

**Mapping error on indexing (vector dimension mismatch)**
The `dense_vector` dimensions in Elasticsearch must match the embedding model exactly (e.g. 1536 for `text-embedding-3-small`). If you switched models, delete the ES index and re-index:
```bash
curl -X DELETE "http://localhost:9200/drupal_content"
ddev drush search-api:reset-tracker drupal_content && ddev drush search-api:index drupal_content
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
```bash
ddev stop && cd drupal-ai-agent
ddev config --project-type=drupal11 --docroot=web && ddev start
```

---

## Quick Reference

| Task | Command |
|---|---|
| Start DDEV (with ES) | `cd drupal-ai-agent && ddev start` |
| Stop DDEV | `ddev stop` |
| Restart DDEV | `ddev restart` |
| Reindex | `ddev drush search-api:reset-tracker drupal_content && ddev drush search-api:index drupal_content` |
| Check ES indices | `curl "http://localhost:9200/_cat/indices?v"` |
| Check ES health | `curl "http://localhost:9200/_cluster/health?pretty"` |
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
