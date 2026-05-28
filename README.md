# StorageAgents

Small multi-agent warehouse simulation for a 10x10 grid.

The important design point is that there is no central scheduler. The shared
backend component is only a message transport. Decisions live inside agents:

- `OrderAgent` creates an order and announces an auction.
- `RobotAgent` instances independently decide whether to bid.
- `OrderAgent` assigns its own order to the best received bid, then waits for
  an explicit accept/reject response.
- `RobotAgent` decides when it needs charging.
- `ChargingStationAgent` grants or queues charging requests.

This is a compact implementation of a contract-net style protocol: announce,
bid, retry if no bids, award, accept/reject, execute, report. Task messages are immutable
snapshots, so agents cannot silently mutate another agent's state by holding the
same Python object reference.

## Why it is not a central orchestrator

`run_demo.py` is only a bootstrap file: it creates agents and starts their async
loops. It does not choose robots, move robots, assign chargers, or inspect bids.

The message flow is:

```text
OrderAgent -> all robots: task.announced
RobotAgent -> OrderAgent: bid.proposed
OrderAgent -> all observers: task.waiting
OrderAgent -> selected robot: task.assigned
RobotAgent -> OrderAgent: task.accepted | task.rejected
RobotAgent -> all robots: cell.requested
RobotAgent -> all observers: robot.path_planned
RobotAgent -> OrderAgent: task.completed
RobotAgent -> ChargingStationAgent: charge.requested
ChargingStationAgent -> RobotAgent: charge.granted
```

The message bus is intentionally simple: it stores queues and delivers
`Envelope` objects by topic/recipient. It does not read task content and does not
make business decisions.

## Navigation

Robots use A* path planning around shelf cells. A shelf is treated as an
obstacle, so the robot drives to a neighboring access cell to pick the item.

Before moving to the next cell, a robot broadcasts `cell.requested` with a local
priority. Other robots listen to peer positions and recent cell requests. If the
next cell is occupied or a higher-priority robot is requesting it, the robot
waits, adds a small backoff, and replans. Priority ages upward for robots that
have waited several times, which avoids permanently favoring the same robot ID.
Robots also commit to a short yield window after losing a right-of-way dispute;
this prevents two agents from repeatedly changing their routes at the same time.
If the same conflict repeats, the yielding robot looks for a nearby free
side-step cell to clear the lane. There is still no central navigation
controller.

The web UI draws each robot's currently planned route with dotted lines.
Robots estimate whether they can safely accept a task by planning the full route:
current position to shelf access, then packaging, then the nearest charger, plus
a reserve and traffic margin. Charging decisions use the same workload estimate
instead of a fixed "below 40%" rule. While moving, a robot also re-checks the
remaining task route before each step; if a detour or traffic conflict would
make the reserve unsafe, it rejects the task back to the auction and goes to
charge instead of driving itself empty.

The UI separates completed, waiting, expired, and failed orders and shows current
robot utilization plus average completion time.

## Run

```bash
python3 run_demo.py
```

Web visualization:

```bash
python3 run_web.py
```

Then open:

```text
http://127.0.0.1:8000
```

Useful options:

```bash
python3 run_demo.py --duration 45 --robots 4 --orders 20
python3 run_demo.py --duration 8 --no-clear
python3 run_web.py --port 8765 --robots 4 --orders 100 --max-auction-retries 3
python3 run_web.py --learning --learning-dir learning_state
```

## Learning mode

Conflict resolution can run with a small Q-learning policy layered on top of the
rule-based safety logic:

```bash
python3 run_demo.py --learning --duration 120 --orders 200
python3 run_web.py --learning --learning-dir learning_state
```

The learned right-of-way policy is stored in:

```text
learning_state/conflict_policy.json
```

Conflict metrics are appended as JSONL events:

```text
learning_state/metrics.jsonl
```

The learning layer only chooses between local conflict actions such as waiting
or taking a side-step. It does not bypass battery, reachability, or task safety
checks, so the agents remain safe while improving their conflict behavior across
runs.

## Tests

```bash
python3 -B -m unittest discover -s tests
```

## Defense phrase

"The single backend component is used only as message transport. The decision
logic is distributed: each agent owns its state, makes local decisions, and
communicates with other agents through asynchronous queues."
