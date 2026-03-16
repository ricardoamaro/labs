# Architecting a Production-Grade AI Agent with Drupal and Elasticsearch

This tutorial guides you through creating an AI-powered content search system using Drupal as your content repository and Elasticsearch with ELSER (Elastic Learned Sparse EncodeR) for semantic search. By the end you'll have a working system that can answer complex questions using your private Drupal content through hybrid keyword + semantic search.

## Architecture

```
Drupal (content) → drupal/ai (AI abstraction) → drupal/search_api_elasticsearch_client → Elasticsearch (ELSER + vector search)
```

## Prerequisites

- **Docker** (version 20.10 or higher)
- **DDEV** (latest version)
- **Git**
- **curl**
- **Node.js** and **npm** (for MCP Inspector, optional)
- At least **8GB RAM** and **30GB free disk space**
- Ports **9200** (Elasticsearch), **5601** (Kibana), and **33000** (DDEV) available

> **Linux users:** Note that `host.docker.internal` is not automatic on Linux — this tutorial handles that for you.

### Verify prerequisites

```bash
docker --version        # 20.10+
ddev version            # latest
git --version
curl --version
node --version          # optional, for MCP Inspector
```

---

## Section 1: Environment Setup

### 1.1 Clone the Repository

The repository includes pre-configured Drupal scaffolding and the `elastic-start-local` setup, so you don't need to run `composer create-project` from scratch.

```bash
git clone https://gitlab.com/ricardoamaro/labs.git
cd labs/DrupalIberia2026/drupal-ai-agent
```

### 1.2 Configure DDEV

> **Important:** Run this from inside the `drupal-ai-agent` directory, not from a parent directory. DDEV will attach to whatever directory you're in.

```bash
ddev config --project-type=drupal11 --docroot=web
ddev start
```

**Expected output:**

```text
Successfully started drupal-ai-agent
Your project can be reached at http://drupal-ai-agent.ddev.site
```

Verify it's running:

```bash
ddev status
# Project name should be 'drupal-ai-agent', not a parent directory name
```

---

## Section 2: Install Drupal Modules

### 2.1 Set minimum stability

Several AI and search modules are pre-stable. Set this once to avoid `@alpha` flags on every require:

```bash
ddev composer config minimum-stability alpha
ddev composer config prefer-stable true
```

### 2.2 Install modules via Composer

```bash
ddev composer require \
  'drush/drush' \
  'drupal/ai' \
  'drupal/ai_agents' \
  'drupal/modeler_api' \
  'drupal/ai_provider_litellm' \
  'drupal/search_api' \
  'drupal/search_api_elasticsearch_client' \
  'drupal/search_api_attachments' \
  'elasticsearch/elasticsearch'
```

> **Note:** `elasticsearch/elasticsearch` is the official PHP client. `search_api_elasticsearch_client` requires it but intentionally does not bundle it so you can match your Elasticsearch version.


### 2.3 Install Drupal

```bash
ddev drush site:install --account-name=admin --account-pass=admin --yes
```

**Expected output:**

```text
You are about to DROP all tables in your 'db' database...
Installation complete. User name: admin  User password: admin
```

### 2.4 Enable modules

```bash
ddev drush pm:enable \
  ai \
  ai_search \
  ai_provider_litellm \
  modeler_api ai_agents ai_agents_explorer ai_agents_extra ai_agents_extra_tools \
  ai_chatbot ai_assistant_api ai_api_explorer \
  search_api search_api_attachments \
  search_api_elasticsearch_client \
  --yes
```

Verify they're active:

```bash
ddev drush pm:list --status=enabled | grep -E "(ai|elasticsearch|search_api)"
```

**Expected output:**

```text
 AI         AI Core (ai)                                            Enabled
 Search     Search API (search_api)                                 Enabled
 Search     Search API Elasticsearch Client (search_api_...)        Enabled
```

---

## Section 3: Elasticsearch Setup

### 3.1 Start Elasticsearch and Kibana

The `elastic-start-local` directory is already included in the repository. Before starting it, you need to make two configuration changes so Elasticsearch is reachable from inside the DDEV Docker network.

**Fix 1 — Bind Elasticsearch to all interfaces (not just localhost):**

```bash
# Check current port binding
cat elastic-start-local/docker-compose.yml | grep -A3 "ports:"
```

Edit `elastic-start-local/docker-compose.yml` and change the Elasticsearch port mapping from:

```yaml
ports:
  - 127.0.0.1:${ES_LOCAL_PORT}:9200
```

to:

```yaml
ports:
  - 0.0.0.0:${ES_LOCAL_PORT}:9200
```

Also add `network.host=0.0.0.0` to the Elasticsearch environment variables (under `xpack.license.self_generated.type=trial`):

```yaml
environment:
  - discovery.type=single-node
  - ELASTIC_PASSWORD=${ES_LOCAL_PASSWORD}
  - xpack.security.enabled=true
  - xpack.security.http.ssl.enabled=false
  - xpack.license.self_generated.type=trial
  - network.host=0.0.0.0        # ← add this
  - xpack.ml.use_auto_machine_memory_percent=true
```

**Start Elasticsearch:**

```bash
./elastic-start-local/start.sh
```

### 3.2 Verify Elasticsearch is accessible

Source the generated credentials, then check cluster health:

```bash
source ./elastic-start-local/.env
curl -X GET "http://localhost:9200/_cluster/health?pretty" \
  -u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected output:**

```json
{
  "cluster_name" : "docker-cluster",
  "status" : "green",
  "timed_out" : false,
  "number_of_nodes" : 1
}
```

### 3.3 Find the Docker Bridge IP (Linux only)

On Linux, DDEV containers cannot reach `localhost` on the host using `host.docker.internal`. You need the Docker bridge gateway IP instead:

```bash
docker network inspect ddev-drupal-ai-agent_default | grep Gateway
# Typically returns 172.x.x.1 or 192.168.x.1
```

Or from inside DDEV:

```bash
ddev exec ip route | grep default | awk '{print $3}'
```

Save this IP — you'll use it as the Elasticsearch URL in Drupal's configuration. For example: `http://172.20.0.1:9200`

**Verify connectivity from inside DDEV:**

```bash
source elastic-start-local/.env
ES_PASS=${ES_LOCAL_PASSWORD}
HOST_IP=$(docker network inspect ddev-drupal-ai-agent_default | jq -r '.[0].IPAM.Config[0].Gateway')
cd drupal-ai-agent
ddev exec curl -s "http://${HOST_IP}:9200/_cluster/health" -u "elastic:${ES_PASS}"
```

> **macOS/Windows users:** `host.docker.internal` works automatically. Use `http://host.docker.internal:9200` instead.

---

## Section 4: Configure Elasticsearch AI (ELSER)

ELSER (Elastic Learned Sparse EncodeR) is Elasticsearch's built-in semantic search model. It enables understanding of intent and context — not just keyword matching.

> **Requirements:** Elasticsearch 8.11+ (your `elastic-start-local` version should meet this).

### 4.1 Deploy the ELSER model

```bash
source ./elastic-start-local/.env

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

**Expected output:**

```json
{
  "inference_id": "elser-model",
  "task_type": "sparse_embedding",
  "service": "elasticsearch"
}
```

> **Note:** The first time you deploy ELSER, Elasticsearch downloads the model (~500MB). This can take several minutes. Check download progress:

```bash
curl "http://localhost:9200/_ml/trained_models/.elser_model_2_linux-x86_64/_stats" \
  -u "elastic:${ES_LOCAL_PASSWORD}" | python3 -m json.tool | grep -A3 "deployment_stats"
```

### 4.2 Verify the ELSER model is ready

```bash
curl "http://localhost:9200/_inference/sparse_embedding/elser-model" \
  -u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected output:**

```json
{
  "endpoints": [{
    "inference_id": "elser-model",
    "task_type": "sparse_embedding",
    "service": "elasticsearch",
    "service_settings": {
      "num_allocations": 1,
      "num_threads": 1,
      "model_id": ".elser_model_2_linux-x86_64"
    }
  }]
}
```

### 4.3 Test ELSER with a semantic query

```bash
curl -X POST "http://localhost:9200/_inference/sparse_embedding/elser-model" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{"input": "GDPR data protection requirements"}'
```

A successful response returns a sparse vector of weighted terms — this is what ELSER uses for semantic matching.

---

## Section 5: Connect Drupal to Elasticsearch

### 5.1 Configure the Search API server

Open the Drupal admin interface:

```bash
ddev launch
# Opens http://drupal-ai-agent.ddev.site in your browser
# Login: admin / admin
```

Navigate to **Administration → Configuration → Search and Metadata → Search API** (`/admin/config/search/search-api`).

Click **Add server** and configure:

| Field | Value |
|---|---|
| Server name | `Elasticsearch` |
| Backend | `Elasticsearch Client` |
| Elasticsearch URL | `http://172.x.x.1:9200` *(use your Docker bridge IP from Section 2.3)* |
| Authentication | Basic auth |
| Username | `elastic` |
| Password | *(from `elastic-start-local/.env`)* |

Click **Save** — you should see a green "The Elasticsearch server could be reached" confirmation.

Alternatively, configure via Drush:

```bash
# Get your bridge IP and password
ddev drush eval "
\$config = \Drupal::configFactory()->getEditable('search_api.server.elasticsearch');
\$config->set('backend', 'elasticsearch_client');
\$config->set('backend_config.connector', 'standard');
\$config->set('backend_config.connector_config.url', 'http://${HOST_IP}:9200');
\$config->save();
echo 'Server configured.';
"
```

### 5.2 Create a Search API index

Navigate to **Search API → Add index**:

| Field | Value |
|---|---|
| Index name | `Drupal Content` |
| Data sources | `Content` |
| Server | `Elasticsearch` (created above) |

Add fields to index (under **Fields** tab):

- `Title` → type: Fulltext
- `Body` → type: Fulltext
- `Content type` → type: String
- `Published` → type: Boolean

Enable the **ELSER Semantic Search** processor if available, or configure it under **Processors**.

Save and then **Index all items** to push existing content to Elasticsearch.

---

## Section 6: Create and Search Content

### 6.1 Create sample content

Via the browser at `/node/add/article`.

Create several articles on different topics so search results are meaningful.

### 6.2 Index content

```bash
ddev drush search-api:index
```

**Expected output:**

```text
Indexed 3 items from index Drupal Content.
```

### 6.3 Run a hybrid search (keyword + semantic)

Test the search index directly via Elasticsearch to verify ELSER is working:

```bash
source ./elastic-start-local/.env

curl -X POST "http://localhost:9200/drupal_content/_search" \
  -H 'Content-Type: application/json' \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  -d '{
    "query": {
      "sparse_vector": {
        "field": "body_vector",
        "inference_id": "elser-model",
        "query": "What are the requirements for data privacy compliance?"
      }
    }
  }'
```

The semantic search understands that "data privacy compliance" relates to GDPR content even without exact keyword matches.

---

## Section 7: MCP Server Integration (Optional / Advanced)

The `drupal/mcp_server` module enables external AI assistants (Claude, Cursor, etc.) to interact with your Drupal content via the Model Context Protocol.

> **Current status:** This module is very early-stage and its Composer dependency chain has a known bug (`drupal/simple_oauth_21` does not exist). Install manually until fixed upstream:

```bash
# Install dependencies that do resolve correctly
ddev composer require \
  'drupal/simple_oauth:^6' \
  'e0ipso/simple_oauth_21:^1@dev' \
  'drupal/tool:^1.0@alpha' \
  'mcp/sdk:^0.4'

# Clone the module directly (bypasses the broken Composer release)
git clone https://git.drupalcode.org/project/mcp_server.git \
  web/modules/contrib/mcp_server

# Enable it
ddev drush pm:enable mcp_server --yes
```

**Test the MCP connection:**

```bash
npx @modelcontextprotocol/inspector ddev drush mcp:stdio
```

**Expected output:**

```text
Connected to MCP Server.
Available Tools: [create_node, update_taxonomy, clear_cache]
Available Resources: [node_list, user_permissions]
```

---

## Section 8: drupal/ai Module Configuration

The `drupal/ai` module provides a unified interface for integrating external LLM providers (OpenAI, Anthropic, Ollama, etc.) with Drupal.

### 8.1 Configure an AI provider

Navigate to **Administration → Configuration → AI → AI Providers** (`/admin/config/ai/providers`).

Add your preferred provider:

| Provider | Requirements |
|---|---|
| OpenAI | API key from platform.openai.com |
| Anthropic | API key from console.anthropic.com |
| Ollama | Local Ollama instance running on host |

For Ollama (fully local, no API key needed):

```bash
# Install Ollama on your host machine first
# https://ollama.com

# Pull a model
ollama pull llama3.2

# The Ollama API runs on localhost:11434
# From DDEV, use the Docker bridge IP: http://172.x.x.1:11434
```

Configure in Drupal at `/admin/config/ai/providers` → Add provider → Ollama → URL: `http://YOUR_BRIDGE_IP:11434`

### 8.2 Test AI integration

```bash
ddev drush eval "
\$ai = \Drupal::service('ai.provider');
\$response = \$ai->chat('What is Drupal?', [], 'default');
echo \$response->getNormalized();
"
```

---

## Section 9: Access Control and Security

Drupal's built-in node access system handles content-level security. Any search results returned by Elasticsearch are filtered through Drupal's access layer before display.

### 9.1 Verify access control is active

```bash
ddev drush eval "
\$node_access = \Drupal::moduleHandler()->moduleExists('node');
echo 'Node access: ' . (\$node_access ? 'Active' : 'Inactive');
\$count = \Drupal::database()->query('SELECT COUNT(*) FROM {node_field_data}')->fetchField();
echo PHP_EOL . 'Indexed nodes: ' . \$count;
"
```

**Expected output:**

```text
Node access: Active
Indexed nodes: 3
```

### 9.2 Verify per-user content visibility

```bash
ddev drush eval "
\$current_user = \Drupal::currentUser();
echo 'Running as user ID: ' . \$current_user->id() . PHP_EOL;
\$result = \Drupal::database()->query(
  'SELECT COUNT(*) as count FROM {node_field_data} WHERE uid = :uid',
  [':uid' => \$current_user->id()]
)->fetchObject();
echo 'Nodes owned by this user: ' . \$result->count;
"
```

---

## Section 10: Monitoring

### 10.1 Elasticsearch cluster health

```bash
source ./elastic-start-local/.env
curl "http://localhost:9200/_cluster/health?pretty" \
  -u "elastic:${ES_LOCAL_PASSWORD}"
```

### 10.2 ELSER model status

```bash
curl "http://localhost:9200/_ml/trained_models/.elser_model_2_linux-x86_64/_stats" \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  | python3 -m json.tool | grep -E "(state|allocation_count)"
```

### 10.3 Search index stats

```bash
curl "http://localhost:9200/drupal_content/_stats" \
  -u "elastic:${ES_LOCAL_PASSWORD}" \
  | python3 -m json.tool | grep -E "(doc_count|store_size)"
```

### 10.4 Set Drupal logging level

```bash
ddev drush config:set system.logging error_level verbose -y
```

### 10.5 Drupal site Agent with knowledge of all your documents

A chatbot on your Drupal site that answers questions using your content, powered by an LLM + ELSER semantic search against Elasticsearch.


**Step 1 — Install the OpenAI provider module:**

```bash
ddev composer require 'drupal/ai_provider_openai'
ddev drush pm:enable ai_provider_openai --yes
```

**Step 2 — Store your API key securely using the Key module:**

```bash
ddev composer require 'drupal/key'
ddev drush pm:enable key --yes
```

Go to `/admin/config/system/keys/add`:

| Field | Value |
|---|---|
| Key name | `openai_api_key` |
| Key type | `Authentication` |
| Key provider | `Configuration` |
| Key value | `sk-...` *(your OpenAI API key)* |

**Step 3 — Configure the OpenAI provider:**

Go to `/admin/config/ai/providers` → **OpenAI** → select your key → Save.

Test it immediately:

```bash
ddev drush pm:enable ai_api_explorer --yes
ddev launch /admin/config/ai/explorer
```

Type a prompt — if you get a response, the LLM connection works.

---

**Step 4 — Enable the chatbot:**

```bash
ddev drush pm:enable ai_chatbot --yes
ddev drush cr
```

Go to `/admin/config/ai/chatbot` and configure:

| Field | Value |
|---|---|
| LLM provider | OpenAI |
| Model | `gpt-5-mini` (cheap) or `gpt-5` |
| System prompt | `You are a helpful assistant. Answer questions using the provided Drupal content.` |

**Step 5 — Connect it to your Elasticsearch search index:**

```bash
ddev drush pm:enable ai_search --yes
```

Go to `/admin/config/ai/search` and point it at your `drupal_content` Search API index. This makes the chatbot retrieve relevant content from Elasticsearch before sending it to the LLM — this is the RAG (Retrieval Augmented Generation) pattern.

**The flow will be:**
```
User question
  → ai_search queries drupal_content index via ELSER (semantic search)
  → top results passed as context to OpenAI
  → OpenAI answers using your Drupal content
  → response shown in chatbot
```




### 10.6 Kibana Agent Builder in Elastic

The Kibana Agent Builder in Elastic 9.x lets you build conversational AI agents that can query your Elasticsearch data. Here's what's actually possible with your current setup:

**Query your indexed Drupal content** — ask natural language questions that get translated into ES|QL or search queries against your `drupal_content` index:
- "Find articles about GDPR compliance"
- "What content do we have about data privacy?"
- "Summarize our most recent articles"

**Use ES|QL tools** — you can give the agent pre-built queries as tools it can invoke:
```
FROM drupal_content | WHERE title LIKE "*GDPR*" | KEEP title, body
```

**Hybrid search** — combine keyword and ELSER semantic search so the agent finds relevant content even without exact keyword matches.

**What it can't do** without extra work:

- It can't write back to Drupal (read-only against Elasticsearch)
- It can't access content not indexed into Elasticsearch
- It has no knowledge of Drupal's access control — that lives in Drupal, not ES

**The more interesting integration is actually the reverse** — using `drupal/ai` + an LLM provider to build agents *inside Drupal* that use Elasticsearch as their knowledge base. That gives you:

- Drupal-native access control on results
- Content creation/editing via AI Automators
- AI chatbot on your site via `ai_chatbot`




## Section 11: Service Management

### Start all services

```bash
# Start Elasticsearch + Kibana
./elastic-start-local/start.sh

# Start Drupal
cd drupal-ai-agent
ddev start
```

### Stop all services

```bash
./elastic-start-local/stop.sh
ddev stop
```

### View logs

```bash
# Elasticsearch logs
cd elastic-start-local && docker compose logs -f elasticsearch

# Drupal/PHP logs
ddev logs
```

### Restart from clean state

```bash
./elastic-start-local/stop.sh && ./elastic-start-local/start.sh
ddev restart
```

---

## Troubleshooting

### Composer stability errors

If you see `minimum-stability` errors when adding new modules:

```bash
ddev composer config minimum-stability alpha
ddev composer config prefer-stable true
```

### Elasticsearch not reachable from DDEV (Linux)

`host.docker.internal` is not automatic on Linux. Get the bridge IP:

```bash
ddev exec ip route | grep default | awk '{print $3}'
```

Use that IP (e.g. `172.20.0.1`) as the Elasticsearch host in all Drupal configuration.

### Elasticsearch bound to localhost only

If `curl http://BRIDGE_IP:9200` fails from the host, the port binding needs fixing:

1. Edit `elastic-start-local/docker-compose.yml`
2. Change `127.0.0.1:${ES_LOCAL_PORT}:9200` → `0.0.0.0:${ES_LOCAL_PORT}:9200`
3. Add `- network.host=0.0.0.0` to the Elasticsearch environment section
4. Run `./elastic-start-local/stop.sh && ./elastic-start-local/start.sh`

### DDEV project named incorrectly

If `ddev status` shows the wrong project name (e.g. `labs` instead of `drupal-ai-agent`), you ran `ddev config` from a parent directory. Fix:

```bash
ddev stop
cd drupal-ai-agent
ddev config --project-type=drupal11 --docroot=web
ddev start
```

### `ddev composer create-project` fails with subdirectory error

Use DDEV's built-in wrapper without a path argument and without `--no-interaction`:

```bash
ddev composer create-project drupal/recommended-project
```

### Ports already in use

```bash
netstat -tulpn | grep -E ":(9200|5601|33000)"
```

If port 80 is busy, DDEV automatically falls back to port 33000.

### ELSER model download stuck

The `.elser_model_2_linux-x86_64` model is ~500MB. Check download status:

```bash
source ./elastic-start-local/.env
curl "http://localhost:9200/_ml/trained_models/.elser_model_2_linux-x86_64" \
  -u "elastic:${ES_LOCAL_PASSWORD}" | python3 -m json.tool | grep "state"
```

Wait until `state: "started"` before using it for inference.

---

## Quick Reference

| Task | Command |
|---|---|
| Start all services | `./elastic-start-local/start.sh && ddev start` |
| Stop all services | `./elastic-start-local/stop.sh && ddev stop` |
| Install Drupal | `ddev drush site:install --account-name=admin --account-pass=admin --yes` |
| Enable modules | `ddev drush pm:enable MODULE_NAME --yes` |
| Index content | `ddev drush search-api:index` |
| Open Drupal admin | `ddev launch /admin` |
| Open Kibana | `http://localhost:5601` |
| ES cluster health | `curl http://localhost:9200/_cluster/health -u elastic:PASSWORD` |
| Get Docker bridge IP | `ddev exec ip route \| grep default \| awk '{print $3}'` |
| View Elasticsearch password | `cat elastic-start-local/.env \| grep ES_LOCAL_PASSWORD` |

---

## Cleanup / Deprovisioning

### Stop and remove DDEV project

```bash
cd drupal-ai-agent
ddev stop
ddev delete -O -y
```

> Your code and configuration files remain on disk. Run `ddev start` to restore.

### Remove Elasticsearch

```bash
./elastic-start-local/uninstall.sh
```

### Deep Docker cleanup (optional)

```bash
docker volume prune -f
docker image prune -f
```

### Complete removal

```bash
cd ..
rm -rf drupal-ai-agent elastic-start-local
```

---

## Additional Resources

- [Drupal AI Module](https://www.drupal.org/project/ai)
- [Search API Elasticsearch Client](https://www.drupal.org/project/search_api_elasticsearch_client)
- [ELSER Documentation](https://www.elastic.co/guide/en/machine-learning/current/ml-nlp-elser.html)
- [Elasticsearch Inference API](https://www.elastic.co/guide/en/elasticsearch/reference/current/inference-apis.html)
- [DDEV Documentation](https://ddev.readthedocs.io/)
- [MCP Server Module](https://www.drupal.org/project/mcp_server)
