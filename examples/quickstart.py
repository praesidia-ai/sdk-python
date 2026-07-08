"""
Praesidia Python SDK — quickstart example.

Run against a local dev backend::

    cd sdk-python
    pip install -e .                         # installs httpx + the SDK itself
    python examples/quickstart.py
"""

from praesidia import Praesidia

client = Praesidia(
    api_key="sk-...",
    org_id="your-org-id",
    base_url="http://localhost:5001",
)

# --------------------------------------------------------------------------
# 1. List agents
# --------------------------------------------------------------------------
agents = client.agents.list(page=1, limit=10)
print(f"Found {len(agents)} agents")
for agent in agents:
    print(f"  - {agent.get('name', agent.get('id'))}")

# --------------------------------------------------------------------------
# 2. Submit an agent task through a connection
#    POST /tasks binds CreateAgentTaskDto — connectionId (a UUID) is REQUIRED,
#    along with a task type and a non-empty input object.
# --------------------------------------------------------------------------
connections = client.connections.list()
if connections:
    connection_id = connections[0]["id"]
    task = client.agents.run(
        connection_id,
        input={"message": "Hello, agent!"},
        type="MESSAGE",
    )
    print(f"\nTask created: {task['id']}, status: {task['status']}")

# --------------------------------------------------------------------------
# 3. List workflows
# --------------------------------------------------------------------------
workflows = client.workflows.list()
print(f"\nFound {len(workflows)} workflows")

# --------------------------------------------------------------------------
# 4. Cost trends (last 30 days)
# --------------------------------------------------------------------------
trends = client.analytics.cost_trends(period="30d")
print(f"\nCost trends: {trends}")

# --------------------------------------------------------------------------
# 5. Stream recent audit events (first 10)
# --------------------------------------------------------------------------
print("\nRecent audit events:")
count = 0
for event in client.audit.stream():
    print(f"  [{event.get('createdAt', '?')}] {event.get('action', '?')}")
    count += 1
    if count >= 10:
        break
