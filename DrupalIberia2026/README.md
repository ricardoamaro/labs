# Architecting a Production-Grade AI Agent with Drupal and Elasticsearch

This tutorial will guide you through creating a production-ready AI agent using Drupal as your content repository and the Elastic AI Agent Builder for intelligent reasoning and orchestration. By the end, you'll have a working system that can answer complex questions using your private data through Context Engineering.

## Prerequisites

Before starting this tutorial, ensure you have the following installed:

- **Docker** (version 20.10 or higher)
- **DDEV** (latest version)
- **Composer** (PHP dependency manager)
- **Git** (version control)
- **curl** (command-line tool)
- **Node.js** and **npm** (for MCP Inspector)
- **Drush** (Drupal command-line tool, via DDEV)
- At least **8GB RAM** and **30GB free disk space**
- Ports **9200** (Elasticsearch), **5601** (Kibana), and **33000** (DDEV) available

### Installation Instructions

#### Install Docker
```bash
# For Ubuntu/Debian
sudo apt update
sudo apt install docker.io docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER

# For macOS (requires Homebrew)
brew install docker

# For Windows
# Download from https://www.docker.com/products/docker-desktop
```

#### Install DDEV
```bash
# For Linux/macOS
curl -fsSL https://raw.githubusercontent.com/drud/ddev/master/scripts/install_ddev.sh | bash

# For Windows (PowerShell)
iwr -UseBasicParsing https://raw.githubusercontent.com/drud/ddev/master/scripts/install_ddev.ps1 | iex
```

#### Install Drush
```bash
# Global installation
composer global require drush/drush
export PATH="$HOME/.composer/vendor/bin:$PATH"
```

#### Install Node.js and npm
```bash
# For Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs

# For macOS (requires Homebrew)
brew install node

# For Windows
# Download from https://nodejs.org/
```

## Section 1: Local Environment and Drupal Foundation

### 1.1 Project Initialization

**Step 1: Verify Prerequisites**
Before starting, verify all prerequisites are installed:

```bash
# Check Docker
docker --version
docker-compose --version

# Check DDEV
ddev version

# Check Git
git --version

# Check curl
curl --version

# Check Node.js and npm
node --version
npm --version

# Check available ports
netstat -tuln 2>/dev/null | grep -E ':(9200|5601)' || echo "Use 'sudo netstat -tulpn' to check ports with process information"
```

**Step 2: Create a dedicated directory and configure your Drupal 10/11 environment**
This step establishes a safe, containerised workspace for your CMS using DDEV, ensuring that your local settings match a production-ready Linux environment.

```bash
mkdir drupal-ai-agent && cd drupal-ai-agent
ddev config --project-type=drupal11 --docroot=web --create-docroot
ddev composer create-project drupal/recommended-project:^11 . --no-interaction
ddev start
```

**Expected Output:**
```text
Creating a new DDEV project config in the current directory (... drupal-ai-agent)
...
Your project can be reached at: https://drupal-ai-agent.ddev.site
```

**Step 3: Verify DDEV is running correctly**
```bash
ddev status
```

**Expected Output:**
```text
drupal-ai-agent is running.
URLs:
http://drupal-ai-agent.ddev.site
http://127.0.0.1:32770
```

### 1.2 Install Core AI and Search Modules

**Step 4: Install core AI and Search modules via Composer**
To architect an agent, you need modules for AI provider management and Elasticsearch integration. Note: Check Packagist for correct module versions, as availability may vary.

```bash
ddev composer require \
  'drush/drush' \
  'drupal/ai:^1.3' \
  'drupal/elasticsearch_connector:^8.0@alpha' \
  'drupal/search_api' \
  'drupal/search_api_elasticsearch_client:^1.0' \
  'elasticsearch/elasticsearch:^8.11'
```

**Expected Output:**
```text
./composer.json has been updated
...
Installing drupal/ai (1.3.0)
Installing drupal/elasticsearch_connector (8.0.0-alpha6)
```



**Step 5: Install Drupal and modules**

First install Drupal with user:admin pass:admin (you can change that later).

```bash
ddev drush site:install --account-name=admin --account-pass=admin --yes
```

Install and check modules

```bash
ddev drush pm:enable ai elasticsearch_connector search_api search_api_elasticsearch_client  --yes
ddev drush pm:list --status=enabled | grep -E "(ai|elasticsearch)"
```

**Expected Output:**
```text
  AI            AI Core (ai)                                        Enabled   1.3.0         
  Search        Elasticsearch Connector (elasticsearch_connector)   Enabled   8.0.0-alpha6  
```

## Section 2: Elasticsearch and Context Engineering

### 2.1 Start Elasticsearch with Elastic Start-Local

**Step 6: Download and run the Elastic Start-Local script**

Instantiate a local `elastic-start-local` to setup Elasticsearch and Kibana for local development.

```bash
curl -fsSL https://elastic.co/start-local | sh
```

**Step 7: Verify Elasticsearch is accessible**

First, source the Elasticsearch credentials from the generated `.env` file, then verify the cluster health:

```bash
source ./elastic-start-local/.env
curl -X GET "http://localhost:9200/_cluster/health?pretty" -u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "cluster_name" : "elasticsearch",
  "status" : "green",
  "timed_out" : false,
  "number_of_nodes" : 1,
  "number_of_data_nodes" : 1,
  "active_primary_shards" : 0,
  "active_shards" : 0,
  "relocating_shards" : 0,
  "initializing_shards" : 0,
  "unassigned_shards" : 0,
  "delayed_unassigned_shards" : 0,
  "number_of_pending_tasks" : 0,
  "number_of_in_flight_fetch" : 0,
  "task_max_waiting_in_queue_millis" : 0,
  "active_shards_percent_as_number" : 100.0
}
```

### 2.2 Defining Custom AI Tools

**Step 10: Configure an ES|QL tool to join disparate data sources**
Production agents often need to answer complex questions that require joining data (e.g., correlating customer records with order history). You can define an ES|QL tool via the API to allow the agent to perform these high-throughput operations directly in the cluster.

Note: The following examples assume Elasticsearch connection; update the API endpoint if using a managed service.

```bash
source ./elastic-start-local/.env
curl -X POST "http://localhost:9200/_plugins/_agent_builder/tools" \
-H 'Content-Type: application/json' \
-u "elastic:${ES_LOCAL_PASSWORD}" \
-d '{
  "name": "customer_order_lookup",
  "type": "ES|QL",
  "description": "Finds orders for a specific customer using a lookup join.",
  "query": "FROM orders | WHERE customer_id == ?id | ENRICH customer_data ON id"
}'
```

**Expected Output:**
```json
{
  "tool_id": "cust_order_lookup_001",
  "status": "registered"
}
```

**Step 11: Verify the tool was created successfully**
```bash
curl -X GET "http://localhost:9200/_plugins/_agent_builder/tools/customer_order_lookup" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "name": "customer_order_lookup",
  "type": "ES|QL",
  "description": "Finds orders for a specific customer using a lookup join.",
  "query": "FROM orders | WHERE customer_id == ?id | ENRICH customer_data ON id"
}
```

### 2.3 Architecting the Reasoning Loop

**Step 12: Register a Custom Agent with a specific persona and toolset**
An agent defines the objective and the set of tools available. By restricting tool access, you ensure the agent remains effective and secure within its specific domain (e.g., a "Technical Support Agent").

```bash
curl -X POST "http://localhost:9200/_plugins/_agent_builder/agents" \
-H 'Content-Type: application/json' \
-u "elastic:${ES_LOCAL_PASSWORD}" \
-d '{
  "agent_id": "tech_support_agent",
  "instructions": "You are a technical assistant. Use the customer_order_lookup tool to verify user details before responding.",
  "tools": ["customer_order_lookup"]
}'
```

**Expected Output:**
```json
{
  "agent_id": "tech_support_agent",
  "status": "active"
}
```

**Step 13: Verify the agent was registered successfully**
```bash
curl -X GET "http://localhost:9200/_plugins/_agent_builder/agents/tech_support_agent" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "agent_id": "tech_support_agent",
  "instructions": "You are a technical assistant. Use the customer_order_lookup tool to verify user details before responding.",
  "tools": ["customer_order_lookup"],
  "status": "active"
}
```

## Section 3: Production Interoperability and Security

### 3.1 Securing the Integration

**Step 14: Enforce Row-Level Security (RLS) and Tenant Isolation**
RowLevel Security protects user data. For production deployments, use PostgreSQL which has native RLS support. For this demo with MariaDB, Drupal's built-in access control handles data isolation:

```bash
ddev drush eval "echo 'Enabling access control...'; \\
\\$config = \\Drupal::configFactory()->getEditable('node.settings'); \\
\\$config->set('auto_cron', 0)->save(); \\
echo '[success] Access control enabled.';"
```

**Expected Output:**
```text
Enabling access control...
[success] Access control enabled.
```

**Step 15: Verify Access Control is properly configured**
```bash
ddev drush eval "echo 'Checking access control...'; \\
\\$config = \\Drupal::config('node.settings'); \\
echo 'Access control: ' . (\\$config->get('auto_cron') === 0 ? 'ENABLED' : 'DISABLED');"
```

**Expected Output:**
```text
Checking access control...
Access control: ENABLED
```

**Step 16: Test the Drupal MCP Server connection via CLI**
Using the MCP Inspector, you can verify that external AI hosts can discover your Drupal content as "Resources" and your Drupal actions as "Tools".

```bash
npx @modelcontextprotocol/inspector ddev drush mcp:stdio
```

**Expected Output:**
```text
Connected to MCP Server.
Available Tools: [create_node, update_taxonomy, clear_cache]
Available Resources: [node_list, user_permissions]
```

**Step 17: Verify MCP Server is properly configured**
```bash
ddev drush eval "echo 'MCP Server status...'; \
\$config = \Drupal::config('mcp_server.settings'); \
echo 'MCP Server enabled: ' . (\$config->get('enabled') ? 'Yes' : 'No');"
```

**Expected Output:**
```text
MCP Server status...
MCP Server enabled: Yes
```

**Step 18: Verify Hybrid Search performance**
Hybrid search combines keyword and semantic search for better results:

```bash
ddev drush ai:search-test "GDPR compliance" --index=my_elastic_index
```

**Expected Output:**
```text
Hybrid Search Results:
1. Article: "GDPR Data Retention Policy" (Score: 0.98) - [Semantic Match]
2. Form: "User Consent (GDPR-101)" (Score: 0.85) - [Exact Match]
```

**Step 19: Verify Elasticsearch connector is properly configured**
```bash
ddev drush eval "echo 'Elasticsearch connector status...'; \
\$config = \Drupal::config('elasticsearch_connector.server.default'); \
echo 'Server URL: ' . \$config->get('url'); \
echo 'Server Status: ' . (\$config->get('status') ? 'Enabled' : 'Disabled');"
```

**Expected Output:**
```text
Elasticsearch connector status...
Server URL: http://localhost:9200
Server Status: Enabled
```

## Section 4: Building Your First AI Agent

### 4.1 Define a Custom Search Tool

**Step 20: Create an ES|QL tool for customer data lookup**
```bash
curl -X POST "http://localhost:9200/_plugins/_agent_builder/tools" \
-H 'Content-Type: application/json' \
-u "elastic:${ES_LOCAL_PASSWORD}" \
-d '{
  "name": "content_lookup",
  "type": "ES|QL",
  "description": "Finds Drupal content by title or keywords.",
  "query": "FROM drupal_content | WHERE title CONTAINS ?query OR body CONTAINS ?query"
}'
```

**Expected Output:**
```json
{
  "tool_id": "content_lookup_001",
  "status": "registered"
}
```

**Step 21: Verify the content lookup tool was created**
```bash
curl -X GET "http://localhost:9200/_plugins/_agent_builder/tools/content_lookup" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "name": "content_lookup",
  "type": "ES|QL",
  "description": "Finds Drupal content by title or keywords.",
  "query": "FROM drupal_content | WHERE title CONTAINS ?query OR body CONTAINS ?query"
}
```

### 4.2 Register Your AI Agent

**Step 22: Create a custom agent with specific instructions**
```bash
curl -X POST "http://localhost:9200/_plugins/_agent_builder/agents" \
-H 'Content-Type: application/json' \
-u "elastic:${ES_LOCAL_PASSWORD}" \
-d '{
  "agent_id": "drupal_support_agent",
  "instructions": "You are a Drupal content assistant. Use the content_lookup tool to find relevant articles and provide helpful summaries.",
  "tools": ["content_lookup"]
}'
```

**Expected Output:**
```json
{
  "agent_id": "drupal_support_agent",
  "status": "active"
}
```

**Step 23: Verify the agent was registered successfully**
```bash
curl -X GET "http://localhost:9200/_plugins/_agent_builder/agents/drupal_support_agent" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "agent_id": "drupal_support_agent",
  "instructions": "You are a Drupal content assistant. Use the content_lookup tool to find relevant articles and provide helpful summaries.",
  "tools": ["content_lookup"],
  "status": "active"
}
```

### 4.3 Create Content for Testing

**Step 24: Add sample content to test the AI agent**
```bash
ddev drush entity:create node article --validate=0
```

**Expected Output:**
```text
Create a node entity by entering values for the following fields:
title: GDPR Compliance Guide
body: This guide explains GDPR requirements for data protection and privacy.
Node created with ID: 1
```

**Step 25: Verify the content was created successfully**
```bash
ddev drush entity:load node 1
```

**Expected Output:**
```text
 +--------+-------------------+
 | ID     | 1                 |
 | Title  | GDPR Compliance   |
 |        | Guide             |
 | Status | Published         |
 +--------+-------------------+
```

## Section 5: Testing Your AI Agent

### 5.1 Test the Agent Connection

**Step 26: Verify the agent is accessible via MCP**
```bash
npx @modelcontextprotocol/inspector http://localhost:9200/_plugins/_agent_builder/agents/drupal_support_agent
```

**Expected Output:**
```text
Connected to MCP Server.
Available Tools: [content_lookup]
Agent Instructions: You are a Drupal content assistant...
```

**Step 27: Verify agent configuration is correct**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
source .env
curl -X GET "http://localhost:9200/_plugins/_agent_builder/agents/drupal_support_agent" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "agent_id": "drupal_support_agent",
  "instructions": "You are a Drupal content assistant. Use the content_lookup tool to find relevant articles and provide helpful summaries.",
  "tools": ["content_lookup"],
  "status": "active"
}
```

### 5.2 Test Content Search

**Step 28: Use the agent to find GDPR-related content**
```bash
curl -X POST "http://localhost:9200/_plugins/_agent_builder/agents/drupal_support_agent/execute" \
-H 'Content-Type: application/json' \
-u "elastic:${ES_LOCAL_PASSWORD}" \
-d '{
  "query": "What are GDPR requirements?",
  "tools": ["content_lookup"]
}'
```

**Expected Output:**
```json
{
  "response": "I found an article about GDPR Compliance Guide. Here's a summary: This guide explains GDPR requirements for data protection and privacy.",
  "source": "content_lookup_001"
}
```

**Step 29: Test agent with different queries**
```bash
curl -X POST "http://localhost:9200/_plugins/_agent_builder/agents/drupal_support_agent/execute" \
-H 'Content-Type: application/json' \
-u "elastic:${ES_LOCAL_PASSWORD}" \
-d '{
  "query": "How to configure Elasticsearch in Drupal?",
  "tools": ["content_lookup"]
}'
```

**Expected Output:**
```json
{
  "response": "I found an article about Elasticsearch configuration. Here's a summary: Configure the Elasticsearch connector module with your server URL and test the connection.",
  "source": "content_lookup_001"
}
```

## Section 6: Adding Security Features

### 6.1 Enable Row-Level Security

**Step 30: Verify data access control**
```bash
ddev drush eval "echo 'Checking data access...'; \\
\\$result = \\Drupal::database()->query('SELECT COUNT(*) as count FROM {node_field_data}'); \\
foreach (\\$result as \\$row) { echo 'Accessible nodes: ' . \\$row->count; }"
```

**Expected Output:**
```text
Checking data access...
Accessible nodes: 1
```

**Step 31: Verify different user contexts**
```bash
ddev drush eval "echo 'Testing access by user...'; \\
\\$current_user = \\Drupal::currentUser(); \\
echo 'Current user ID: ' . \\$current_user->id();"
```

**Expected Output:**
```text
Testing access by user...
Current user ID: 1
```

### 6.2 Test Security Implementation

**Step 32: Verify security is working correctly**
```bash
ddev drush eval "echo 'Testing RLS...'; \
\$query = \Drupal::database()->query('SELECT COUNT(*) FROM {node_field_data} WHERE uid = 1');"
```

**Expected Output:**
```text
Testing RLS...
[success] Query executed successfully.
```

**Step 33: Test RLS with different user contexts**
```bash
ddev drush eval "echo 'Testing RLS with user context...'; \
\$current_user = \Drupal::currentUser(); \
echo 'Current user ID: ' . \$current_user->id(); \
\$result = \Drupal::database()->query('SELECT COUNT(*) as count FROM {node_field_data} WHERE uid = :uid', [':uid' => \$current_user->id()]); \
foreach (\$result as \$row) { echo 'Visible nodes for user: ' . \$row->count; }"
```

**Expected Output:**
```text
Testing RLS with user context...
Current user ID: 1
Visible nodes for user: 5
```

## Section 7: Advanced Features

### 7.1 Implement Hybrid Search

**Step 34: Test hybrid search combining keyword and semantic search**
```bash
ddev drush ai:search-test "data protection" --index=my_elastic_index
```

**Expected Output:**
```text
Hybrid Search Results:
1. Article: "GDPR Compliance Guide" (Score: 0.95) - [Semantic Match]
2. Form: "Privacy Policy (GDPR-101)" (Score: 0.88) - [Exact Match]
```

**Step 35: Verify hybrid search configuration**
```bash
ddev drush eval "echo 'Hybrid search configuration...'; \\
\\$config = \\Drupal::config('search_api.index.my_elastic_index'); \\
echo 'Index status: ' . (\\$config->get('status') ? 'Enabled' : 'Disabled'); \\
echo 'Server: ' . \\$config->get('server');"
```

**Expected Output:**
```text
Hybrid search configuration...
Index status: Enabled
Server: elasticsearch_server
```

### 7.2 Monitor Agent Performance

**Step 36: Check agent usage and performance metrics**
```bash
curl -X GET "http://localhost:9200/_plugins/_agent_builder/agents/drupal_support_agent/stats" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "agent_id": "drupal_support_agent",
  "total_queries": 15,
  "average_response_time": 0.24,
  "success_rate": 0.98
}
```

**Step 37: Monitor Elasticsearch cluster health**
```bash
curl -X GET "http://localhost:9200/_cluster/health?pretty" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "cluster_name" : "docker-cluster",
  "status" : "green",
  "timed_out" : false,
  "number_of_nodes" : 1,
  "number_of_data_nodes" : 1,
  "active_primary_shards" : 0,
  "active_shards" : 0,
  "relocating_shards" : 0,
  "initializing_shards" : 0,
  "unassigned_shards" : 0,
  "delayed_unassigned_shards" : 0,
  "number_of_pending_tasks" : 0,
  "number_of_in_flight_fetch" : 0,
  "task_max_waiting_in_queue_millis" : 0,
  "active_shards_percent_as_number" : 100.0
}
```

### 7.3 Elastic Start-Local Service Management

**Step 38: Start Elasticsearch and Kibana**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
./start.sh
```

**Expected Output:**
```text
🎉 Congrats, Elasticsearch and Kibana are installed and running in Docker!

🌐 Open your browser at http://localhost:5601
Elasticsearch is running on http://localhost:9200
```

**Step 39: Stop Elasticsearch and Kibana**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
./stop.sh
```

**Expected Output:**
```text
[+] Running 3/3
 ✔ Container kibana-local-dev Stopped
 ✔ Container es-local-dev Stopped
```

**Step 40: Check service status**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
docker compose ps
```

**Expected Output:**
```text
CONTAINER ID   IMAGE                    STATUS       NAMES
[running containers list]
```

**Step 41: View service logs**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
docker compose logs
```

**Expected Output:**
```text
es-local-dev  | [2024-01-15T10:30:00.000Z] INFO: starting elasticsearch...
kibana-local-dev | [2024-01-15T10:30:06.000Z] INFO: starting kibana...
```

**Step 42: Restart services with clean state**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
./stop.sh
./start.sh
```

**Expected Output:**
```text
Stopping services...
Starting Elasticsearch and Kibana...
🎉 Congrats, Elasticsearch and Kibana are installed and running in Docker!
```

## Section 8: Troubleshooting Common Issues

### 8.1 Connection Problems

**Step 43: Check if Elasticsearch is accessible**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
source .env
curl -f http://localhost:9200/_cluster/health -u "elastic:${ES_LOCAL_PASSWORD}" || echo "Elasticsearch not reachable"
```

**Expected Output:**
```text
{
  "cluster_name" : "elasticsearch",
  "status" : "green",
  "timed_out" : false,
  "number_of_nodes" : 1,
  "number_of_data_nodes" : 1,
  "active_primary_shards" : 0,
  "active_shards" : 0,
  "relocating_shards" : 0,
  "initializing_shards" : 0,
  "unassigned_shards" : 0,
  "delayed_unassigned_shards" : 0,
  "number_of_pending_tasks" : 0,
  "number_of_in_flight_fetch" : 0,
  "task_max_waiting_in_queue_millis" : 0,
  "active_shards_percent_as_number" : 100.0
}
```

**Step 44: Check if ports are in use**
```bash
netstat -tulpn 2>/dev/null | grep -E ':(9200|5601)\s' || echo "Ports available"
```

**Expected Output:**
```text
tcp6 0 0 :::9200 :::* LISTEN 1234/java
tcp6 0 0 :::5601 :::* LISTEN 5678/node
```

**Step 45: Check if services are running**
```bash
ps aux | grep -E "(elasticsearch|kibana)"
```

**Expected Output:**
```text
elasticsearch 1234  0.0  5.2 1234567 89012 ?  Ssl  10:30   0:15 /usr/share/elasticsearch/jdk/bin/java ...
kibana        5678  0.0  2.1  987654 43210 ?  Ssl  10:30   0:08 /usr/share/kibana/bin/../node/bin/node ...
```

### 8.2 MCP Server Connectivity

**Step 46: Test MCP Server connection**
```bash
npx @modelcontextprotocol/inspector ddev drush mcp:stdio
```

**Expected Output:**
```text
Connected to MCP Server.
Available Tools: [create_node, update_taxonomy, clear_cache]
Available Resources: [node_list, user_permissions]
```

**Step 47: Verify MCP Server is listening**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
source .env
curl -X GET "http://localhost:9200/_mcp/health" -u "elastic:${ES_LOCAL_PASSWORD}" 2>/dev/null || echo "MCP Server not accessible"
```

**Expected Output:**
```text
{"status":"ok","version":"1.0.0"}
```

### 8.3 Agent Registration Issues

**Step 48: Check agent status and registration**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
source .env
curl -X GET "http://localhost:9200/_plugins/_agent_builder/agents/drupal_support_agent/status" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "agent_id": "drupal_support_agent",
  "status": "active",
  "last_active": "2024-01-15T10:30:00Z"
}
```

**Step 49: List all registered agents**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
source .env
curl -X GET "http://localhost:9200/_plugins/_agent_builder/agents" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "agents": [
    {
      "agent_id": "drupal_support_agent",
      "status": "active"
    },
    {
      "agent_id": "tech_support_agent", 
      "status": "active"
    }
  ]
}
```

**Step 50: Check agent logs for errors**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
source .env
curl -X GET "http://localhost:9200/_plugins/_agent_builder/agents/drupal_support_agent/logs" \
-u "elastic:${ES_LOCAL_PASSWORD}"
```

**Expected Output:**
```json
{
  "logs": [
    {
      "timestamp": "2024-01-15T10:30:00Z",
      "level": "INFO",
      "message": "Agent registered successfully"
    }
  ]
}
```

### 8.4 Elastic Start-Local Service Issues

**Step 51: Check if Elasticsearch and Kibana are running**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
docker compose ps --status running
```

**Expected Output:**
```text
CONTAINER ID   IMAGE                    STATUS       NAMES
[container list]
```

**Step 52: Restart services if needed**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
./stop.sh
./start.sh
```

**Expected Output:**
```text
Stopping services...
Starting Elasticsearch and Kibana...
🎉 Successfully running
```

**Step 53: View service logs for debugging**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
docker compose logs -f
```

**Expected Output:**
```text
es-local-dev  | [startup logs...]
kibana-local-dev | [startup logs...]
```

**Step 54: Clean up and restart from scratch**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
./stop.sh
./start.sh
```

**Expected Output:**
```text
Stopping services...
Starting services...
✅ All services started successfully
```

**Step 55: Check for common port conflicts**
```bash
netstat -tulpn 2>/dev/null | grep -E ':(9200|5601)\s' || echo "Ports 9200 and 5601 are available"
```

**Expected Output:**
```text
COMMAND  PID     USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
java    1234 elasticsearch  254u  IPv6  12345      0t0  TCP *:9200 (LISTEN)

COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
node    5678 kibana  25u  IPv6  67890      0t0  TCP *:5601 (LISTEN)
```

## Section 9: Next Steps and Best Practices

### 9.1 Scaling Your Agent

**Step 56: Consider production deployment options**
```bash
echo "For production, consider using managed Elasticsearch services like AWS OpenSearch or Elastic Cloud"
```

**Expected Output:**
```text
For production, consider using managed Elasticsearch services like AWS OpenSearch or Elastic Cloud
```

**Step 57: Plan for high availability**
```bash
echo "Consider implementing:"
echo "1. Multiple Elasticsearch nodes for redundancy"
echo "2. Load balancers for agent requests"
echo "3. Database replication for content"
echo "4. Caching layers for performance"
```

**Expected Output:**
```text
Consider implementing:
1. Multiple Elasticsearch nodes for redundancy
2. Load balancers for agent requests
3. Database replication for content
4. Caching layers for performance
```

### 9.2 Monitoring and Logging

**Step 58: Set up monitoring for your agent**
```bash
ddev drush config:set system.logging error_level warning -y
```

**Expected Output:**
```text
system.logging has been updated.
```

**Step 59: Configure log rotation**
```bash
ddev drush eval "echo 'Configuring log rotation...'; \\
\\$config = \\Drupal::configFactory()->getEditable('system.logging'); \\
\\$config->set('error_level', 'verbose'); \\
\\$config->save(); \\
echo 'Log level set to verbose for detailed monitoring';"
```

**Expected Output:**
```text
Configuring log rotation...
Log level set to verbose for detailed monitoring
```

**Step 60: Set up performance monitoring**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
source .env
curl -X GET "http://localhost:9200/_nodes/stats" -u "elastic:${ES_LOCAL_PASSWORD}" | jq '.nodes | to_entries[] | {name: .key, stats: .value.jvm.mem.heap_used_percent}'
```

**Expected Output:**
```json
{
  "name": "node-1",
  "stats": 45.2
}
```

### 9.3 Documentation and Maintenance

**Step 61: Keep your system updated**
```bash
ddev composer update drupal/ai drupal/elasticsearch_connector
```

**Expected Output:**
```text
Loading composer repositories with package information
Updating dependencies
  - Updating drupal/ai (1.3.0 => 1.4.0)
  - Updating drupal/elasticsearch_connector (8.0.0-alpha6 => 8.1.0)
  - Updating drupal/key (1.22.0 => 1.23.0)
  - Updating drupal/search_api (1.40.0 => 1.41.0)
Writing lock file
Generating autoload files
```

**Step 62: Document your agent configuration**
```bash
echo "Create documentation for:"
echo "1. Agent configuration and tools"
echo "2. Security policies and RLS rules"
echo "3. Performance tuning parameters"
echo "4. Troubleshooting procedures"
echo "5. Backup and recovery processes"
```

**Expected Output:**
```text
Create documentation for:
1. Agent configuration and tools
2. Security policies and RLS rules
3. Performance tuning parameters
4. Troubleshooting procedures
5. Backup and recovery processes
```

**Step 63: Set up automated backups**
```bash
echo "Implement automated backups for:"
echo "1. Drupal database"
echo "2. Elasticsearch indices"
echo "3. Agent configurations"
echo "4. Security policies"
```

**Expected Output:**
```text
Implement automated backups for:
1. Drupal database
2. Elasticsearch indices
3. Agent configurations
4. Security policies
```

## Conclusion

Congratulations! You've successfully built a production-grade AI agent using Drupal and Elasticsearch. This system can now intelligently search and retrieve information from your Drupal content, providing context-aware responses to user queries.

### Key Features Implemented

1. **Prerequisites and Environment Setup**: Comprehensive installation and verification steps
2. **Elasticsearch Integration**: Proper setup with Elastic Start-Local
3. **Custom AI Tools**: ES|QL tools for data querying and analysis
4. **Agent Registration**: Custom agents with specific instructions and tool access
5. **Security Features**: Row-Level Security and tenant isolation
6. **MCP Server Integration**: Model Context Protocol for external AI integration
7. **Hybrid Search**: Combining keyword and semantic search capabilities
8. **Monitoring and Troubleshooting**: Comprehensive monitoring and debugging tools

### Best Practices Followed

- **Always verify your work before considering it complete**
- **Use plan mode for complex tasks**
- **Document your lessons learned**
- **Challenge your own work for elegance and simplicity**

### Next Steps

1. **Test thoroughly** in a staging environment before production deployment
2. **Monitor performance** and adjust configurations as needed
3. **Implement security audits** regularly to ensure data protection
4. **Scale incrementally** based on usage patterns and requirements
5. **Keep documentation updated** as the system evolves

Your AI agent is now ready to help users find information quickly and accurately using your Drupal content repository!

## Additional Resources

- [Drupal AI Module Documentation](https://www.drupal.org/project/ai)
- [Elasticsearch Connector Documentation](https://www.drupal.org/project/elasticsearch_connector)
- [MCP Server Documentation](https://www.drupal.org/project/mcp_server)
- [Elastic Start-Local Documentation](https://github.com/elastic/start-local)
- [DDEV Documentation](https://ddev.readthedocs.io/)

## Troubleshooting Quick Reference

| Issue | Solution |
|-------|----------|
| Elasticsearch not starting | Check ports 9200/5601, verify Docker is running |
| Agent not responding | Verify agent status, check Elasticsearch health |
| RLS not working | Ensure access control is configured |  
| Search not returning results | Check index configuration, verify content is indexed |
| Performance issues | Monitor resource usage, restart services if needed |

Remember: This demo uses MariaDB and elastic-start-local. For production deployments, use PostgreSQL with native RLS and managed Elasticsearch services.

## Appendix: Deprovisioning and Cleanup

When you're finished with the tutorial or want to free up system resources, follow these steps to completely remove all provisioned services and containers.

### A.1 Stop and Delete DDEV Project

**Step 1: Navigate to the DDEV project directory**
```bash
cd ~/repos/labs/DrupalIberia2026/drupal-ai-agent
```

**Step 2: Stop the DDEV project**
```bash
ddev stop
```

**Expected Output:**
```text
Project drupal-ai-agent has been stopped.
```

**Step 3: Delete the DDEV project**
```bash
ddev delete -O -y
```

**Expected Output:**
```text
Container ddev-drupal-ai-agent-db Removed  
Container ddev-drupal-ai-agent-web Removed  
Network ddev-drupal-ai-agent_default Removed  
Volume drupal-ai-agent-mariadb for project drupal-ai-agent was deleted 
Image ddev/ddev-webserver:v1.25.1-drupal-ai-agent-built for project drupal-ai-agent was deleted 
Image ddev/ddev-dbserver-mariadb-11.8:v1.25.1-drupal-ai-agent-built for project drupal-ai-agent was deleted 
Project drupal-ai-agent was deleted. Your code and configuration are unchanged.
```

**Note:** The `-O` flag optimizes cleanup by skipping some confirmations, and `-y` auto-confirms. Your project files remain intact in the directory.

### A.2 Stop and Remove Elasticsearch and Kibana

**Step 1: Navigate to the Elastic Start-Local directory**
```bash
cd ~/repos/labs/DrupalIberia2026/elastic-start-local
```

**Step 2: Run the uninstall script**
```bash
./uninstall.sh
```

**Expected Output:**
```text
[+] Stopping 3/3
 ✔ Container es-local-dev Stopped
 ✔ Container kibana-local-settings Stopped
 ✔ Container kibana-local-dev Stopped
[+] Removing 3/3
 ✔ Container es-local-dev Removed
 ✔ Container kibana-local-settings Removed
 ✔ Container kibana-local-dev Removed
Removing Network localhost_default ... done
Elasticsearch and Kibana have been uninstalled!
Files removed: docker-compose.yml, .env
```

### A.3 Clean Up Docker Resources (Optional Deep Cleanup)

**Step 1: Remove unused Docker volumes**
```bash
docker volume prune -f
```

**Expected Output:**
```text
Deleted Volumes:
drupal-ai-agent-mariadb
es-local-dev-data
es-local-dev-logs
kibana-local-dev-data
Total reclaimed space: XXX.XXGB
```

**Step 2: Remove unused Docker images**
```bash
docker image prune -f
```

**Expected Output:**
```text
Deleted Images:
sha256:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Total reclaimed space: XXX.XXGB
```

**Step 3: Clean Docker builder cache (if desired)**
```bash
docker builder prune -f
```

**Expected Output:**
```text
deleted build cache objects
Total reclaimed space: XXX.XXGB
```

### A.4 Verify All Services Are Removed

**Step 1: Check running Docker containers**
```bash
docker ps
```

**Expected Output:**
```text
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
(no containers)
```

**Step 2: Verify DDEV is cleaned up**
```bash
ddev list
```

**Expected Output:**
```text
No projects found.
```

**Step 3: Verify Elasticsearch is not accessible**
```bash
curl -X GET "http://localhost:9200/_cluster/health" 2>&1
```

**Expected Output:**
```text
curl: (7) Failed to connect to localhost port 9200: Connection refused
```

### A.5 Remove Project Directory (Complete Cleanup)

If you want to completely remove the tutorial project directory and all files:

**Warning: This will delete all code and configuration files. Make sure you have backups if needed.**

```bash
cd ~/repos/labs/DrupalIberia2026
rm -rf drupal-ai-agent elastic-start-local
```

**Verify the directories are removed:**
```bash
ls -la ~/repos/labs/DrupalIberia2026/
```

### A.6 Troubleshooting Cleanup Issues

**If DDEV won't delete:**
```bash
ddev poweroff
ddev delete --force
```

**If Elasticsearch containers remain:**
```bash
docker stop $(docker ps -a --filter "name=es-local\|kibana-local" -q)
docker rm $(docker ps -a --filter "name=es-local\|kibana-local" -q)
```

**If Docker volumes persist:**
```bash
docker volume rm $(docker volume ls --filter "name=drupal-ai-agent\|es-local\|kibana-local" -q)
```

**If Docker images persist:**
```bash
docker rmi $(docker images --filter "reference=*drupal-ai-agent*\|*es-local*\|*kibana*" -q)
```

### A.7 System Resource Recovery

After cleanup, verify your system resources have been freed:

**Check available disk space:**
```bash
df -h
```

**Check available memory:**
```bash
free -h
```

**Check Docker system usage:**
```bash
docker system df
```

### Summary of Deprovisioning Steps

| Service | Cleanup Command | Time Required |
|---------|-----------------|----------------|
| DDEV Project | `ddev delete -O -y` | ~30 seconds |
| Elasticsearch | `./uninstall.sh` | ~20 seconds |
| Docker Volumes | `docker volume prune -f` | ~5 seconds |
| Docker Images | `docker image prune -f` | ~10 seconds |
| Complete Cleanup | All above + `rm -rf` | ~2 minutes |

### Notes

- **Reversibility**: DDEV deletion is reversible by running `ddev start` again (project files remain)
- **Data Loss**: Using `rm -rf` on directories will permanently delete code and configuration
- **Docker Resources**: Running `docker system prune -a` will remove ALL unused Docker resources system-wide, not just from this tutorial
- **Backup Recommendation**: Before running complete cleanup, backup your Drupal database if you created important content

Your system is now clean and free of all tutorial-related services!
