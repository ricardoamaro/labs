# Drupal AI Agent with Elasticsearch

Build a RAG-powered AI agent inside Drupal that answers questions using your content, indexed in Elasticsearch with ELSER semantic search, answered by an LLM.

```
User question → Drupal AI Agent → RAG search (Search API)
  → Elasticsearch ELSER vectors → LiteLLM → Grounded answer
```

## Prerequisites

- Docker 20.10+, DDEV (latest), Git, curl, jq
- LiteLLM proxy running with at least one model
- 8GB RAM, 30GB free disk
- Ports 9200 (Elasticsearch), 5601 (Kibana), 33000 (DDEV) free

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
  'drupal/search_api_elasticsearch_client:^1.0' \
  'drupal/search_api_attachments' \
  'elasticsearch/elasticsearch:^8.11'
```

> **Never install `drupal/elasticsearch_connector`** — it conflicts with `search_api_elasticsearch_client` and crashes the Search API server form with a `PluginException`.

### 2.3 Install Drupal and enable modules

```bash
ddev drush site:install --account-name=admin --account-pass=admin --yes

ddev drush pm:enable \
  ai ai_search ai_provider_litellm key \
  modeler_api ai_agents ai_agents_explorer ai_agents_extra ai_agents_extra_tools \
  ai_chatbot ai_assistant_api ai_api_explorer \
  search_api search_api_attachments search_api_elasticsearch_client \
  --yes
```

Verify:

```bash
ddev drush pm:list --status=enabled | grep -E "(ai|search_api|elasticsearch)"
```

---

## Section 3: Elasticsearch Setup

### 3.1 Edit docker-compose.yml before starting

Two edits are required so Elasticsearch is reachable from inside the DDEV Docker network. Run from the **repo root** (not inside `drupal-ai-agent`):

```bash
# Change port binding from localhost-only to all interfaces
sed -i 's/127.0.0.1:${ES_LOCAL_PORT}/0.0.0.0:${ES_LOCAL_PORT}/' \
  elastic-start-local/docker-compose.yml

# Add network.host=0.0.0.0 to Elasticsearch environment
sed -i 's/- xpack.license.self_generated.type=trial/- xpack.license.self_generated.type=trial\n      - network.host=0.0.0.0/' \
  elastic-start-local/docker-compose.yml

# Verify both changes
grep -E "(ES_LOCAL_PORT|network.host)" elastic-start-local/docker-compose.yml
```

### 3.2 Start and verify

```bash
./elastic-start-local/start.sh

source elastic-start-local/.env
curl "http://localhost:9200/_cluster/health?pretty" -u "elastic:${ES_LOCAL_PASSWORD}"
# Expected: "status": "green"
```

### 3.3 Get the Docker bridge IP (Linux only)

On Linux, `host.docker.internal` doesn't work automatically. Get the IP DDEV containers use to reach the host:

```bash
cd drupal-ai-agent
HOST_IP=$(ddev exec ip route | grep default | awk '{print $3}')
echo "Bridge IP: $HOST_IP"

# Verify ES is reachable from inside DDEV
source ../elastic-start-local/.env
ddev exec curl -s "http://${HOST_IP}:9200/_cluster/health" -u "elastic:${ES_LOCAL_PASSWORD}"
```

> **macOS/Windows:** Use `host.docker.internal` instead of the bridge IP everywhere.

---

## Section 4: Configure Search API

### 4.1 Add the Elasticsearch server

Go to `/admin/config/search/search-api/add-server`:

| Field | Value |
|---|---|
| Server name | `Elasticsearch` |
| Backend | `Elasticsearch Client` |
| URL | `http://BRIDGE_IP:9200` (Linux) or `http://host.docker.internal:9200` (Mac/Win) |
| Authentication | Basic — username `elastic`, password from `.env` |

Save — confirm the green "server could be reached" message.

### 4.2 Create the index

Go to `/admin/config/search/search-api/add-index`:

| Field | Value |
|---|---|
| Index name | `Drupal Content` |
| Machine name | `drupal_content` ← must match exactly |
| Datasources | `Content` → Article bundle only |
| Server | `Elasticsearch` |

### 4.3 Add fields

Go to `/admin/config/search/search-api/index/drupal_content/fields` → Add:

| Field | Type |
|---|---|
| Title | **Fulltext** |
| Body | **Fulltext** |
| Content type | String |
| Published | Boolean |

Save changes.

---

## Section 5: ELSER Semantic Search

### 5.1 Deploy the ELSER model

```bash
source elastic-start-local/.env

curl -X PUT "http://localhost:9200/_inference/sparse_embedding/elser-model" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{
    "service": "elasticsearch",
    "service_settings": {
      "num_allocations": 1,
      "num_threads": 1,
      "model_id": ".elser_model_2_linux-x86_64"
    }
  }'
```

> First deployment downloads ~500MB and takes several minutes. If you get a 400 "IDs must be unique" error, ELSER is already deployed — verify with:

```bash
curl "http://localhost:9200/_inference/sparse_embedding/elser-model" \
  -u "elastic:${ES_LOCAL_PASSWORD}"
```

### 5.2 Create index template (survives reindexing)

Without this, every `search-api:clear` destroys the pipeline and you lose your vectors.

```bash
curl -X PUT "http://localhost:9200/_index_template/drupal_content_template" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{
    "index_patterns": ["drupal_content*"],
    "template": {
      "settings": {
        "index": {"default_pipeline": "elser-pipeline"}
      }
    }
  }'
```

### 5.3 Create the ingest pipeline

```bash
curl -X PUT "http://localhost:9200/_ingest/pipeline/elser-pipeline" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{
    "processors": [
      {
        "join": {
          "field": "body",
          "separator": " ",
          "ignore_failure": true
        }
      },
      {
        "inference": {
          "model_id": ".elser_model_2_linux-x86_64",
          "input_output": [{"input_field": "body", "output_field": "body_vector"}],
          "ignore_missing": true
        }
      }
    ]
  }'
```

> `ignore_failure` on `join` handles both string and array formats of `body` — Search API sends it differently depending on context.

### 5.4 Attach pipeline to the index

```bash
curl -X PUT "http://localhost:9200/drupal_content/_settings" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{"index": {"default_pipeline": "elser-pipeline"}}'
```

---

## Section 6: Create Content and Index

### 6.1 Create sample articles

Go to `/node/add/article`. Create at least two articles with rich body text on different topics. Longer, more descriptive content produces better ELSER vectors.

Example: title `GDPR Compliance Guide`, body covering GDPR requirements, data subject rights, 72-hour breach reporting, lawful basis for processing, and step-by-step implementation guidance.

### 6.2 Index

```bash
cd drupal-ai-agent
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
```

### 6.3 Verify ELSER vectors exist

```bash
source ../elastic-start-local/.env
curl "http://localhost:9200/drupal_content/_search?pretty&size=1" \
  -u "elastic:${ES_LOCAL_PASSWORD}" | python3 -m json.tool | grep "body_vector"
# Must show: "body_vector": {
```

If `body_vector` is missing, reattach the pipeline and reindex:

```bash
curl -X PUT "http://localhost:9200/drupal_content/_settings" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{"index": {"default_pipeline": "elser-pipeline"}}'

ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
```

### 6.4 Test semantic search

This query has no "GDPR" keyword — ELSER should still return your GDPR article:

```bash
curl -X POST "http://localhost:9200/drupal_content/_search?pretty" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{
    "query": {
      "sparse_vector": {
        "field": "body_vector",
        "inference_id": "elser-model",
        "query": "data privacy requirements for people in Europe"
      }
    }
  }'
```

A hit with score > 0 confirms the full ELSER pipeline is working.

---

## Section 7: Advanced — Hybrid Search with RRF

Pure ELSER semantic search is powerful but can miss exact keyword matches (SKU numbers, proper nouns, code identifiers). Hybrid search combines ELSER sparse vectors with BM25 keyword scoring using **Reciprocal Rank Fusion (RRF)** — no manual score normalization required.

### 7.1 Test hybrid search via Kibana Dev Tools

Go to `http://localhost:5601/app/dev_tools#/console`:

```json
GET drupal_content/_search
{
  "retriever": {
    "rrf": {
      "retrievers": [
        {
          "standard": {
            "query": {
              "match": { "body": "GDPR compliance" }
            }
          }
        },
        {
          "standard": {
            "query": {
              "sparse_vector": {
                "field": "body_vector",
                "inference_id": "elser-model",
                "query": "data privacy requirements"
              }
            }
          }
        }
      ],
      "rank_constant": 60,
      "rank_window_size": 100
    }
  }
}
```

RRF merges both ranked lists — documents appearing high in both get the strongest boost. This handles cases where ELSER alone would miss an exact product code, but BM25 alone would miss a semantically related concept.

### 7.2 Add the Boost by AI Search processor (optional)

The `ai_search` module ships a **Boost Database by AI Search** processor that blends ELSER results into standard Search API queries. Enable it on the index:

Go to `/admin/config/search/search-api/index/drupal_content/processors` and enable **Boost Database by AI Search**. Configure the AI Search index to `drupal_content` and set a weight between `0.1` (subtle boost) and `1.0` (strong preference for semantic results).

Save and reindex:

```bash
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
```

---

## Section 8: Configure LiteLLM Provider

### 7.1 Verify LiteLLM is running

```bash
source elastic-start-local/.env
curl http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_API_KEY"
```

### 7.2 Store the key and configure the provider

Go to `/admin/config/system/keys/add` and create a key with your `$LITELLM_API_KEY` value.

Then go to `/admin/config/ai/providers` → LiteLLM:

| Field | Value |
|---|---|
| API Key | the key you just created |
| Host | `http://BRIDGE_IP:4000` (Linux) or `http://host.docker.internal:4000` (Mac/Win) |

Save — confirm green status.

---

## Section 9: Create the AI Agent

### 8.1 Create the agent

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

### 8.2 Fix tool settings (UI bug workaround)

The agent UI does not reliably persist tool settings to the config entity. Set directly:

```bash
cd drupal-ai-agent

ddev drush ev "
\$config = \Drupal::configFactory()->getEditable('ai_assistant_api.ai_assistant.content_assistant');
\$config->set('actions_enabled', ['ai_search_rag_search' => 'ai_search_rag_search']);
\$config->save();
echo 'Saved: ';
print_r(\$config->get('actions_enabled'));
"
ddev drush cr
```

### 8.3 Configure RAG tool settings

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

---

## Section 10: Test the Agent

Go to `/admin/config/ai/agents/explore`, select **Content Assistant**, choose a model (e.g. `gemini-2.5-flash`).

**Keyword test:**
> "What articles do we have about GDPR compliance?"

**Semantic test (no keyword overlap):**
> "What do we have about data privacy for people in Europe?"

The second query proves ELSER is working — no "GDPR" keyword, but the article should still be found. The Progress panel should show:

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
| AI Provider | LiteLLM Proxy (use a fast, cheap model like `gemini-2.5-flash-lite`) |

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

### 10.1 Setup

```bash
ddev exec which pdftotext || ddev exec apt-get install -y poppler-utils
```

Go to `/admin/config/search/search-api-attachments` and set extractor to `pdftotext`.

### 10.2 Add a File field to your content type

Go to `/admin/structure/types/manage/article/fields/add-field`. Add a **File** field allowing PDF uploads.

### 10.3 Add file contents to the index

Go to `/admin/config/search/search-api/index/drupal_content/fields` → **Add fields** → find **File contents** under your file field → add as **Fulltext**. Save.

### 10.4 Update the pipeline to vectorize file contents

```bash
source ../elastic-start-local/.env

curl -X PUT "http://localhost:9200/_ingest/pipeline/elser-pipeline" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{
    "processors": [
      {"join": {"field": "body", "separator": " ", "ignore_failure": true}},
      {"join": {"field": "field_file", "separator": " ", "ignore_failure": true}},
      {
        "script": {
          "source": "ctx.combined_text = (ctx.containsKey(\"body\") ? ctx.body : \"\") + \" \" + (ctx.containsKey(\"field_file\") ? ctx.field_file : \"\")",
          "ignore_failure": true
        }
      },
      {
        "inference": {
          "model_id": ".elser_model_2_linux-x86_64",
          "input_output": [{"input_field": "combined_text", "output_field": "body_vector"}],
          "ignore_missing": true
        }
      }
    ]
  }'
```

Upload a PDF to any article, then reindex:

```bash
ddev drush search-api:reset-tracker drupal_content
ddev drush search-api:index drupal_content
```

---

## Section 13: Browse with Kibana

### Create a Data View

Go to `http://localhost:5601/app/management/kibana/dataViews` → **Create data view**:
- Name: `Drupal Content`, Index pattern: `drupal_content`, No timestamp field.

### Discover

Go to `http://localhost:5601/app/discover`, select **Drupal Content**. Expand any row to see `title`, `body`, and `body_vector` (the ELSER sparse embeddings).

### Dev Tools console (`http://localhost:5601/app/dev_tools#/console`)

**Keyword:**
```json
GET drupal_content/_search
{"query": {"match": {"body": "GDPR compliance"}}}
```

**ELSER semantic:**
```json
GET drupal_content/_search
{
  "query": {
    "sparse_vector": {
      "field": "body_vector",
      "inference_id": "elser-model",
      "query": "data privacy requirements for people in Europe"
    }
  }
}
```

**Hybrid:**
```json
GET drupal_content/_search
{
  "query": {
    "bool": {
      "should": [
        {"match": {"body": "data privacy Europe"}},
        {"sparse_vector": {"field": "body_vector", "inference_id": "elser-model", "query": "data privacy requirements for people in Europe"}}
      ]
    }
  }
}
```

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

**`elasticsearch_connector` PluginException on server form**
Both modules are installed — they conflict. Remove `elasticsearch_connector`:
```bash
ddev drush pm:uninstall elasticsearch_connector --yes
ddev composer remove drupal/elasticsearch_connector && ddev drush cr
```

**`body_vector` missing after reindex**
Reattach the pipeline manually, then reindex:
```bash
curl -X PUT "http://localhost:9200/drupal_content/_settings" \
  -H 'Content-Type: application/json' \
  -u "elastic:$ES_LOCAL_PASSWORD" \
  -d '{"index": {"default_pipeline": "elser-pipeline"}}'
ddev drush search-api:reset-tracker drupal_content && ddev drush search-api:index drupal_content
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

**Elasticsearch not reachable from DDEV (Linux)**
```bash
ddev exec ip route | grep default | awk '{print $3}'
```
Use that IP everywhere — `host.docker.internal` only works on Mac/Windows.

**DDEV project named incorrectly**
```bash
ddev stop && cd drupal-ai-agent
ddev config --project-type=drupal11 --docroot=web && ddev start
```

**ELSER model already exists (400 error)**
It's already deployed from a previous session — this is fine. Verify it's working:
```bash
curl "http://localhost:9200/_inference/sparse_embedding/elser-model" -u "elastic:$ES_LOCAL_PASSWORD"
```

---

## Quick Reference

| Task | Command |
|---|---|
| Start all | `./elastic-start-local/start.sh && cd drupal-ai-agent && ddev start` |
| Stop all | `./elastic-start-local/stop.sh && ddev stop` |
| Reindex | `ddev drush search-api:reset-tracker drupal_content && ddev drush search-api:index drupal_content` |
| Reattach pipeline | `curl -X PUT "http://localhost:9200/drupal_content/_settings" -H 'Content-Type: application/json' -u "elastic:$ES_LOCAL_PASSWORD" -d '{"index":{"default_pipeline":"elser-pipeline"}}'` |
| Check vectors | `curl "http://localhost:9200/drupal_content/_search?size=1" -u "elastic:$ES_LOCAL_PASSWORD" \| python3 -m json.tool \| grep body_vector` |
| Agent Explorer | `ddev launch /admin/config/ai/agents/explore` |
| Kibana | `http://localhost:5601` |
| Bridge IP | `ddev exec ip route \| grep default \| awk '{print $3}'` |
| ES password | `grep ES_LOCAL_PASSWORD elastic-start-local/.env` |
| Rebuild cache | `ddev drush cr` |

---

## Cleanup

```bash
cd drupal-ai-agent && ddev stop && ddev delete -O -y
cd .. && ./elastic-start-local/uninstall.sh
docker volume prune -f && docker image prune -f
```

---

## Additional Resources

- [Drupal AI Module](https://www.drupal.org/project/ai)
- [Search API Elasticsearch Client](https://www.drupal.org/project/search_api_elasticsearch_client)
- [ELSER Documentation](https://www.elastic.co/guide/en/machine-learning/current/ml-nlp-elser.html)
- [Elasticsearch Inference API](https://www.elastic.co/guide/en/elasticsearch/reference/current/inference-apis.html)
- [DDEV Documentation](https://ddev.readthedocs.io/)
- [LiteLLM Documentation](https://docs.litellm.ai/)
- [MCP Server Module](https://www.drupal.org/project/mcp_server)
